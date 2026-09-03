"""Unified LLM client for generation, streaming, and Agent tool calls.

Every configured provider uses the same LiteLLM boundary. Provider-specific
model routing and capability rules live in ``core.model_providers``; this
adapter only translates the normalized response into sag_agent events.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from sag_agent import CancellationToken, ModelChunk, ModelRequest, Usage
from sag_agent import ToolCall as RuntimeToolCall
from sag_api.core.config import Settings
from sag_api.core.error_taxonomy import ErrorCode, ErrorLayer, ErrorStage
from sag_api.core.errors import (
    ApiError,
    ConfigurationError,
    ServiceUnavailableError,
    UpstreamError,
)
from sag_api.core.litellm_policy import apply_litellm_completion_policy
from sag_api.core.logging import get_logger

log = get_logger("generation")

Message = dict[str, Any]


def _responses_content(content: Any, *, role: str = "user") -> Any:
    text_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(content, str):
        return [{"type": text_type, "text": content}]
    if not isinstance(content, list):
        return [{"type": text_type, "text": str(content or "")}]
    converted: list[dict[str, Any]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "input_text", "output_text"}:
            converted.append({"type": text_type, "text": str(item.get("text") or "")})
        elif item.get("type") in {"image_url", "input_image"}:
            image = item.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            if url:
                converted.append({"type": "input_image", "image_url": str(url)})
    return converted or [{"type": text_type, "text": ""}]


def _responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": str(message.get("content") or ""),
                }
            )
            continue
        tool_calls = message.get("tool_calls") or []
        for call in tool_calls:
            function = call.get("function") or {}
            items.append(
                {
                    "type": "function_call",
                    "call_id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "arguments": str(function.get("arguments") or "{}"),
                }
            )
        # A tool-call assistant turn is represented by the function_call item
        # above. Do not append an empty input_text message, which Responses API
        # rejects as an invalid assistant content part.
        if not tool_calls or message.get("content"):
            items.append({"role": role, "content": _responses_content(message.get("content", ""), role=role)})
    return items


def _responses_tools(tools: list[dict] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    result: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            continue
        result.append(
            {
                "type": "function",
                "name": function.get("name"),
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                "strict": False,
            }
        )
    return result or None


def _response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _response_tool_calls(payload: Any) -> list[RuntimeToolCall]:
    calls: list[RuntimeToolCall] = []
    if not isinstance(payload, dict):
        return calls
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        raw_arguments = str(item.get("arguments") or "{}")
        try:
            candidate = json.loads(raw_arguments)
            arguments = candidate if isinstance(candidate, dict) else {}
            parse_error = None if isinstance(candidate, dict) else "tool arguments must decode to an object"
        except (TypeError, json.JSONDecodeError) as exc:
            arguments = {}
            parse_error = str(exc)
        calls.append(
            RuntimeToolCall(
                id=str(item.get("call_id") or item.get("id") or ""),
                name=str(item.get("name") or ""),
                arguments=arguments,
                raw_arguments=raw_arguments,
                parse_error=parse_error,
            )
        )
    return calls


async def _litellm_completion(**kwargs: Any) -> Any:
    """Import lazily so an unconfigured server can still start without provider work."""
    from litellm import acompletion

    return await acompletion(**kwargs)


async def _raise_responses_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    await response.aread()
    detail = response.text.strip().replace("\n", " ")[:800]
    raise ResponsesApiError(
        response.status_code,
        detail,
        retry_after=response.headers.get("retry-after"),
    )


class ResponsesApiError(RuntimeError):
    """Native Responses API failure with the HTTP status kept for retry policy."""

    def __init__(self, status_code: int, detail: str, *, retry_after: str | None = None) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"Responses API 返回 HTTP {status_code}: {detail}")


def _responses_retry_delay(error: ResponsesApiError, attempt: int) -> float | None:
    """Return a bounded retry delay only for failures safe to retry before streaming."""

    detail = str(error).casefold()
    # Daily/monthly quota exhaustion cannot recover within this request.
    if error.status_code == 429 and any(token in detail for token in (
        "daily_limit_exceeded",
        "daily usage limit",
        "monthly_limit",
        "insufficient_quota",
    )):
        return None
    if error.status_code not in {408, 409, 429, 500, 502, 503, 504}:
        return None
    try:
        retry_after = float(error.retry_after) if error.retry_after else None
    except ValueError:
        retry_after = None
    # Keep an interactive chat responsive even when an upstream gateway sends
    # an excessive Retry-After value.
    return min(5.0, max(0.25, retry_after if retry_after is not None else 0.5 * (2**attempt)))


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _classify_llm_error(e: Exception, *, stage: ErrorStage) -> ApiError:
    """把 LiteLLM 的裸异常拆分为带 layer/stage 的领域异常。

    历史上所有厂商错误都塌缩成一个 ``UpstreamError``，研发无法区分
    「超时/限流（可重试）」与「鉴权/请求非法（改配置才行）」。这里按
    LiteLLM 的异常类型名与 status_code 做粗分类，全部归到 LLM 层。
    """
    name = type(e).__name__
    status = getattr(e, "status_code", None)
    detail = f"{name}: {e}"
    # 超时 / 限流 / 服务暂不可用 —— 可安全重试
    _retryable_names = {
        "Timeout",
        "APITimeoutError",
        "RateLimitError",
        "ServiceUnavailableError",
        "InternalServerError",
    }
    if name in _retryable_names or status in {408, 429, 503}:
        return ServiceUnavailableError(
            f"模型调用暂时失败（{name}），请稍后重试：{e}",
            code=ErrorCode.LLM_UNAVAILABLE,
            layer=ErrorLayer.LLM,
            stage=stage,
        )
    # 鉴权失败 —— API key / 权限问题，需改配置
    if name in {"AuthenticationError", "PermissionDeniedError"} or status in {401, 403}:
        return ConfigurationError(
            f"模型鉴权失败（{name}），请检查 API Key 配置：{e}",
            code=ErrorCode.LLM_AUTH_ERROR,
            layer=ErrorLayer.LLM,
            stage=ErrorStage.CONFIG,
        )
    # 请求非法 / 上下文超限 —— 请求本身的问题
    if name in {"BadRequestError", "ContextWindowExceededError", "UnprocessableEntityError"} or status in {400, 422}:
        return UpstreamError(
            f"模型拒绝请求（{name}）：{e}",
            code=ErrorCode.LLM_BAD_REQUEST,
            layer=ErrorLayer.LLM,
            stage=stage,
        )
    # 其它一律归为通用生成失败
    return UpstreamError(f"生成失败：{detail}", layer=ErrorLayer.LLM, stage=stage)


class LLMClient:
    def __init__(self, settings: Settings, *, protocol: str = "openai-compatible") -> None:
        self._settings = settings
        self._protocol = protocol

    @property
    def configured(self) -> bool:
        return self._settings.llm_configured

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise ConfigurationError("尚未配置 LLM（SAG_LLM_PROVIDER / SAG_LLM_API_KEY / SAG_LLM_MODEL）")

    async def _create_completion(
        self,
        messages: list[Message],
        *,
        stream: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "model": self._settings.routed_llm_model,
            "api_key": self._settings.llm_api_key,
            "timeout": self._settings.llm_timeout_ms / 1000,
            "num_retries": self._settings.llm_max_retries,
            "messages": messages,
            "temperature": self._settings.effective_llm_temperature,
            "max_tokens": self._settings.llm_max_tokens,
            "stream": stream,
        }
        if tools:
            request["tools"] = tools
            if tool_choice is not None:
                request["tool_choice"] = tool_choice
        if self._settings.llm_base_url:
            request["api_base"] = self._settings.llm_base_url
        request = apply_litellm_completion_policy(self._settings, request)
        try:
            return await _litellm_completion(**request)
        except Exception as error:  # noqa: BLE001
            # Keep provider diagnostics useful without ever logging the API key.
            log.error(
                "模型请求失败 model=%s api_base=%s stream=%s error=%s",
                request.get("model"),
                request.get("api_base") or "默认端点",
                stream,
                error,
            )
            raise

    def _responses_endpoint(self) -> str:
        base = (self._settings.llm_base_url or "").rstrip("/")
        if base.endswith("/responses"):
            return base
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")].rstrip("/")
        return f"{base}/responses"

    async def _create_responses(
        self,
        messages: list[Message],
        *,
        stream: bool = False,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> Any:
        body: dict[str, Any] = {
            "model": self._settings.routed_llm_model,
            "input": _responses_input(messages),
            "max_output_tokens": self._settings.llm_max_tokens,
            "stream": stream,
        }
        if self._settings.effective_llm_temperature is not None:
            body["temperature"] = self._settings.effective_llm_temperature
        response_tools = _responses_tools(tools)
        if response_tools:
            body["tools"] = response_tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        timeout = self._settings.llm_timeout_ms / 1000
        log.info(
            "Responses API 请求 model=%s endpoint=%s stream=%s",
            self._settings.routed_llm_model,
            self._responses_endpoint(),
            stream,
        )
        client = httpx.AsyncClient(timeout=timeout)
        if stream:
            return client, client.stream(
                "POST",
                self._responses_endpoint(),
                headers=headers,
                json=body,
            )
        try:
            for attempt in range(self._settings.llm_max_retries + 1):
                response = await client.post(
                    self._responses_endpoint(),
                    headers=headers,
                    json=body,
                )
                try:
                    await _raise_responses_error(response)
                except ResponsesApiError as error:
                    delay = _responses_retry_delay(error, attempt)
                    if delay is None or attempt >= self._settings.llm_max_retries:
                        raise
                    log.warning(
                        "Responses API 请求失败，将在 %.2fs 后重试 attempt=%s/%s status=%s",
                        delay,
                        attempt + 1,
                        self._settings.llm_max_retries,
                        error.status_code,
                    )
                    await asyncio.sleep(delay)
                    continue
                return response.json()
            raise RuntimeError("Responses API 请求重试耗尽")
        finally:
            await client.aclose()

    async def _response_events(self, messages: list[Message], **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        for attempt in range(self._settings.llm_max_retries + 1):
            client, stream_context = await self._create_responses(messages, stream=True, **kwargs)
            event_state: dict[str, Any] = {"name": "", "data_lines": []}

            async def emit(state: dict[str, Any] = event_state) -> dict[str, Any] | None:
                data_lines = state["data_lines"]
                if not data_lines:
                    return None
                raw = "\n".join(data_lines)
                data_lines.clear()
                if raw == "[DONE]":
                    return {"type": "response.done"}
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return None
                if isinstance(payload, dict) and state["name"] and "type" not in payload:
                    payload["type"] = state["name"]
                return payload if isinstance(payload, dict) else None

            try:
                async with stream_context as response:
                    # No event has reached the caller yet, so retrying a rejected
                    # handshake cannot duplicate any streamed answer or tool call.
                    await _raise_responses_error(response)
                    async for line in response.aiter_lines():
                        if not line:
                            payload = await emit()
                            if payload is not None:
                                yield payload
                            event_state["name"] = ""
                            continue
                        if line.startswith("event:"):
                            event_state["name"] = line[6:].strip()
                        elif line.startswith("data:"):
                            event_state["data_lines"].append(line[5:].lstrip())
                    payload = await emit()
                    if payload is not None:
                        yield payload
                return
            except ResponsesApiError as error:
                delay = _responses_retry_delay(error, attempt)
                if delay is None or attempt >= self._settings.llm_max_retries:
                    raise
                log.warning(
                    "Responses API 流握手失败，将在 %.2fs 后重试 attempt=%s/%s status=%s",
                    delay,
                    attempt + 1,
                    self._settings.llm_max_retries,
                    error.status_code,
                )
                await asyncio.sleep(delay)
            finally:
                await client.aclose()

    async def _stream_responses_turn(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelChunk]:
        calls: dict[str, dict[str, str]] = {}
        completed_payload: dict[str, Any] | None = None
        async for event in self._response_events(
            [message.to_model_dict() for message in request.messages],
            tools=list(request.tools) or None,
            tool_choice=request.tool_choice if request.tools else None,
        ):
            cancellation.raise_if_cancelled()
            event_type = str(event.get("type") or "")
            if event_type == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str) and delta:
                    yield ModelChunk(text_delta=delta)
            elif event_type == "response.function_call_arguments.delta":
                item_id = str(event.get("item_id") or event.get("call_id") or "")
                if item_id:
                    calls.setdefault(item_id, {"id": item_id, "name": "", "arguments": ""})["arguments"] += str(
                        event.get("delta") or ""
                    )
            elif event_type == "response.output_item.added":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    item_id = str(item.get("call_id") or item.get("id") or "")
                    calls[item_id] = {
                        "id": item_id,
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or ""),
                    }
            elif event_type == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "function_call":
                    item_id = str(item.get("call_id") or item.get("id") or "")
                    calls[item_id] = {
                        "id": item_id,
                        "name": str(item.get("name") or calls.get(item_id, {}).get("name") or ""),
                        "arguments": str(item.get("arguments") or calls.get(item_id, {}).get("arguments") or "{}"),
                    }
            elif event_type == "response.completed":
                response = event.get("response")
                if isinstance(response, dict):
                    completed_payload = response

        parsed_calls = _response_tool_calls(completed_payload) if completed_payload else []
        if not parsed_calls:
            parsed_calls = _response_tool_calls(
                {"output": [{**item, "type": "function_call", "call_id": item["id"]} for item in calls.values()]}
            )
        if parsed_calls:
            yield ModelChunk(tool_calls=tuple(parsed_calls), finish_reason="tool_calls")
        else:
            yield ModelChunk(finish_reason="stop")

    @staticmethod
    async def _close_stream(stream: Any) -> None:
        close = getattr(stream, "close", None) or getattr(stream, "aclose", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    async def stream_turn(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelChunk]:
        """Stream one provider turn, including native function calls.

        A direct answer and a tool decision now share one provider request. This is
        the adapter required by sag_agent.ModelProvider.
        """

        self._ensure_configured()
        if self._protocol == "openai-responses":
            try:
                async for chunk in self._stream_responses_turn(request, cancellation):
                    yield chunk
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("Responses API 轮次调用失败：%s", e)
                raise _classify_llm_error(e, stage=ErrorStage.GENERATE) from e
            return

        tool_parts: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        stream = None
        try:
            stream = await self._create_completion(
                [message.to_model_dict() for message in request.messages],
                tools=list(request.tools) or None,
                tool_choice=request.tool_choice if request.tools else None,
                stream=True,
            )
            async for chunk in stream:
                cancellation.raise_if_cancelled()
                raw_usage = _attr(chunk, "usage")
                if raw_usage is not None:
                    prompt_details = _attr(raw_usage, "prompt_tokens_details")
                    completion_details = _attr(raw_usage, "completion_tokens_details")
                    yield ModelChunk(
                        usage=Usage(
                            input_tokens=int(_attr(raw_usage, "prompt_tokens", 0) or 0),
                            output_tokens=int(_attr(raw_usage, "completion_tokens", 0) or 0),
                            cached_tokens=int(_attr(prompt_details, "cached_tokens", 0) or 0),
                            reasoning_tokens=int(_attr(completion_details, "reasoning_tokens", 0) or 0),
                        )
                    )
                choices = _attr(chunk, "choices", []) or []
                if not choices:
                    continue
                choice = choices[0]
                finish_reason = _attr(choice, "finish_reason") or finish_reason
                delta = _attr(choice, "delta", {})
                token = _attr(delta, "content")
                if token:
                    yield ModelChunk(text_delta=token)
                for fallback_index, tool_delta in enumerate(_attr(delta, "tool_calls") or []):
                    index = _attr(tool_delta, "index")
                    index = fallback_index if index is None else int(index)
                    part = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    tool_id = _attr(tool_delta, "id")
                    if tool_id:
                        part["id"] += str(tool_id)
                    function = _attr(tool_delta, "function")
                    if function is not None:
                        name = _attr(function, "name")
                        arguments = _attr(function, "arguments")
                        if name:
                            part["name"] += str(name)
                        if arguments:
                            part["arguments"] += (
                                arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
                            )

            calls: list[RuntimeToolCall] = []
            for index in sorted(tool_parts):
                part = tool_parts[index]
                raw_arguments = part["arguments"] or "{}"
                parse_error = None
                arguments: dict = {}
                try:
                    candidate = json.loads(raw_arguments)
                    if isinstance(candidate, dict):
                        arguments = candidate
                    else:
                        parse_error = "tool arguments must decode to an object"
                except (json.JSONDecodeError, TypeError) as exc:
                    parse_error = str(exc)
                calls.append(
                    RuntimeToolCall(
                        id=part["id"] or f"tool-{request.turn}-{index}",
                        name=part["name"],
                        arguments=arguments,
                        raw_arguments=raw_arguments,
                        parse_error=parse_error,
                    )
                )
            if calls or finish_reason:
                yield ModelChunk(tool_calls=tuple(calls), finish_reason=finish_reason)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("LLM 轮次流式调用失败：%s", e)
            raise _classify_llm_error(e, stage=ErrorStage.GENERATE) from e
        finally:
            if stream is not None:
                try:
                    await self._close_stream(stream)
                except Exception as e:  # noqa: BLE001
                    log.debug("LLM 轮次流关闭失败：%s", e)

    async def complete(self, messages: list[Message]) -> str:
        self._ensure_configured()
        try:
            if self._protocol == "openai-responses":
                response = await self._create_responses(messages)
                text = _response_text(response)
                if not text:
                    raise UpstreamError(
                        "模型未返回候选答案",
                        code=ErrorCode.LLM_EMPTY_RESPONSE,
                        layer=ErrorLayer.LLM,
                        stage=ErrorStage.GENERATE,
                    )
                return text
            resp = await self._create_completion(messages)
            choices = _attr(resp, "choices", []) or []
            if not choices:
                raise UpstreamError(
                    "模型未返回候选答案",
                    code=ErrorCode.LLM_EMPTY_RESPONSE,
                    layer=ErrorLayer.LLM,
                    stage=ErrorStage.GENERATE,
                )
            return _attr(_attr(choices[0], "message", {}), "content", "") or ""
        except ApiError:
            raise
        except Exception as e:  # noqa: BLE001
            raise _classify_llm_error(e, stage=ErrorStage.GENERATE) from e

    async def stream_complete(self, messages: list[Message]) -> AsyncIterator[str]:
        """Stream plain text completion deltas without the Agent/tool protocol."""

        self._ensure_configured()
        if self._protocol == "openai-responses":
            try:
                async for event in self._response_events(messages):
                    if event.get("type") == "response.output_text.delta" and event.get("delta"):
                        yield str(event["delta"])
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                raise _classify_llm_error(e, stage=ErrorStage.GENERATE) from e
            return

        stream = None
        try:
            stream = await self._create_completion(messages, stream=True)
            async for chunk in stream:
                choices = _attr(chunk, "choices", []) or []
                if not choices:
                    continue
                token = _attr(_attr(choices[0], "delta", {}), "content")
                if token:
                    yield token
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            raise _classify_llm_error(e, stage=ErrorStage.GENERATE) from e
        finally:
            # Closing explicitly makes browser aborts release the upstream HTTP
            # connection immediately, even when the stream is only partly read.
            if stream is not None:
                try:
                    await self._close_stream(stream)
                except Exception as e:  # noqa: BLE001
                    log.debug("LLM 流关闭失败：%s", e)
