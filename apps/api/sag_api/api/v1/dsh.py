"""SAG-owned DSH conversation gateway.

The browser only talks to SAG.  A DSH request is given a short-lived delegation
token which is valid for one user, one SAG conversation, and one selected list
of sources.  DSH can use that token solely to call ``/dsh/retrieval``; it never
receives a browser credential or an unrestricted SAG service token.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from sag_api.api.v1.attachments import attachment_path
from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager
from sag_api.core.errors import (
    AuthError,
    ConfigurationError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from sag_api.db.models import Document, DshMessage, DshSession, Source, User
from sag_api.sag import EngineManager
from sag_api.schemas.dsh import (
    DshAttachmentInput,
    DshMessageCreate,
    DshMessageOut,
    DshRetrievalRequest,
    DshSessionCreate,
    DshSessionOut,
    DshSessionPatch,
    DshStreamRequest,
)
from sag_api.services.source_service import search_source_candidates
from sag_api.tools.base import ToolContext
from sag_api.tools.builtin import SearchContextTool

router = APIRouter(prefix="/dsh", tags=["dsh"])
_DELEGATION_AUDIENCE = "sag-dsh-retrieval-v1"
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _safe_filename(value: str, fallback: str) -> str:
    normalized = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", value).strip()
    normalized = re.sub(r"\s+", " ", normalized)[:72].strip(" .")
    return normalized or fallback


def _dsh_title_from_query(query: str, source_names: list[str]) -> str:
    title = re.sub(r"[\r\n]+", " ", query)
    for name in source_names:
        if name.strip():
            title = re.sub(re.escape(f"@{name.strip()}"), " ", title, flags=re.IGNORECASE)
    title = re.sub(r"(^|\s)@[\S]+", r"\1", title)
    title = re.sub(r"\s+", " ", title).strip()[:80]
    return title or "DSH 知识会话"


def _conversation_directory(kind: str) -> Path:
    relative = settings.conversation_archive_dir if kind == "archive" else settings.conversation_ingest_dir
    root = Path(settings.conversation_export_root).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:  # Defensive: settings service already validates this.
        raise ConfigurationError("会话文件目录超出受控挂载范围") from error
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_markdown(kind: str, title: str, content: str) -> str:
    directory = _conversation_directory(kind)
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    stem = f"{_safe_filename(title, 'DSH 会话')}-{stamp}"
    path = directory / f"{stem}.md"
    suffix = 2
    while path.exists():
        path = directory / f"{stem}-{suffix}.md"
        suffix += 1
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return str(path)


def _citation_label(citation: dict[str, Any]) -> str:
    """Use the DSH document title for internal citations in exported Markdown."""

    source_name = str(citation.get("source_name") or "").strip()
    document_name = str(citation.get("document_name") or "").strip()
    title = str(citation.get("title") or "").strip()
    heading = str(citation.get("heading") or "").strip()
    base = document_name or title or heading or source_name or "来源"
    base = re.sub(r"\.(?:md|markdown)$", "", base, flags=re.IGNORECASE)
    if source_name and source_name != base:
        return f"{base} - {source_name}"
    return base


def _message_markdown(message: DshMessage) -> str:
    blocks = [f"## {'用户' if message.role == 'user' else 'DSH'}", "", message.content or "（无文本内容）"]
    if message.attachments:
        blocks.extend(["", "附件："])
        for attachment in message.attachments:
            name = (
                str(attachment.get("name") or attachment.get("id") or "未命名附件")
                if isinstance(attachment, dict)
                else "未命名附件"
            )
            blocks.append(f"- {name}")
    citations = message.context.get("citations") if isinstance(message.context, dict) else None
    if isinstance(citations, list):
        reference_lines: list[str] = []
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            label = _citation_label(citation)
            url = citation.get("url")
            reference_lines.append(f"- [{label}]({url})" if isinstance(url, str) and url else f"- {label}")
        if reference_lines:
            blocks.extend(["", "引用来源：", *reference_lines])
    if message.event_summary:
        blocks.extend(["", "<details>", "<summary>处理过程</summary>", ""])
        for event in message.event_summary:
            if not isinstance(event, dict):
                continue
            label = str(event.get("type") or "事件")
            query = str(event.get("query") or "")
            round_no = event.get("round")
            extra = f" · 第 {round_no} 轮" if isinstance(round_no, int) else ""
            blocks.append(f"- `{label}`{extra}{f'：{query}' if query else ''}")
        blocks.extend(["", "</details>"])
    return "\n".join(blocks)


async def _export_session_markdown(session: AsyncSession, conversation: DshSession) -> str:
    rows = list(
        await session.scalars(
            select(DshMessage).where(DshMessage.session_id == conversation.id).order_by(DshMessage.created_at.asc())
        )
    )
    created = conversation.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    sources = "、".join(conversation.source_ids) if conversation.source_ids else "未选择知识库"
    document = [
        f"# {conversation.title}",
        "",
        f"- 归档时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}",
        f"- 会话创建：{created}",
        f"- 模式：{'深度研究' if conversation.mode == 'agent' else '受控 RAG'}",
        f"- 知识库：{sources}",
        f"- 模型：{conversation.provider} / {conversation.model}",
        "",
        "---",
        "",
    ]
    for message in rows:
        document.extend([_message_markdown(message), "", "---", ""])
    return _write_markdown("archive", conversation.title, "\n".join(document))


@router.get("/sessions", response_model=list[DshSessionOut])
async def list_sessions(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[DshSessionOut]:
    rows = await session.scalars(
        select(DshSession)
        .where(DshSession.user_id == user.id, DshSession.archived.is_(False))
        .order_by(DshSession.updated_at.desc())
    )
    return [DshSessionOut.model_validate(row) for row in rows]


@router.post("/sessions", response_model=DshSessionOut, status_code=201)
async def create_session(
    body: DshSessionCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DshSessionOut:
    row = DshSession(user_id=user.id, **body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DshSessionOut.model_validate(row)


async def _owned_session(session: AsyncSession, user: User, session_id: str) -> DshSession:
    row = await session.scalar(
        select(DshSession).where(
            DshSession.id == session_id,
            DshSession.user_id == user.id,
            DshSession.archived.is_(False),
        )
    )
    if row is None:
        raise NotFoundError("DSH 会话不存在")
    return row


@router.patch("/sessions/{session_id}", response_model=DshSessionOut)
async def update_session(
    session_id: str,
    body: DshSessionPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DshSessionOut:
    row = await _owned_session(session, user, session_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.commit()
    await session.refresh(row)
    return DshSessionOut.model_validate(row)


@router.get("/sessions/{session_id}/messages", response_model=list[DshMessageOut])
async def list_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DshMessageOut]:
    await _owned_session(session, user, session_id)
    rows = await session.scalars(
        select(DshMessage).where(DshMessage.session_id == session_id).order_by(DshMessage.created_at.asc())
    )
    return [DshMessageOut.model_validate(row) for row in rows]


@router.post("/sessions/{session_id}/messages", response_model=DshMessageOut, status_code=201)
async def create_message(
    session_id: str,
    body: DshMessageCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DshMessageOut:
    await _owned_session(session, user, session_id)
    row = DshMessage(session_id=session_id, **body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DshMessageOut.model_validate(row)


@router.post("/sessions/{session_id}/archive")
async def archive_session(
    session_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | bool]:
    row = await _owned_session(session, user, session_id)
    exported_path = await _export_session_markdown(session, row)
    row.archived = True
    await session.commit()
    return {"ok": True, "path": exported_path}


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Permanently remove one active DSH session and its persisted messages."""

    conversation = await _owned_session(session, user, session_id)
    # Keep this explicit in addition to the database FK cascade. Existing local
    # SQLite deployments may have been initialized before foreign keys were on.
    await session.execute(delete(DshMessage).where(DshMessage.session_id == conversation.id))
    await session.delete(conversation)
    await session.commit()
    return {"ok": True}


@router.post("/sessions/{session_id}/messages/{message_id}/save-to-library")
async def save_message_to_library(
    session_id: str,
    message_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | bool]:
    conversation = await _owned_session(session, user, session_id)
    message = await session.scalar(
        select(DshMessage).where(DshMessage.id == message_id, DshMessage.session_id == conversation.id)
    )
    if message is None or message.role != "assistant":
        raise NotFoundError("可入库的 DSH 回答不存在")
    source_line = "、".join(conversation.source_ids) if conversation.source_ids else "未选择知识库"
    document = "\n".join(
        [
            f"# {_safe_filename(conversation.title, 'DSH 回答')}",
            "",
            f"- 保存时间：{datetime.now(UTC).astimezone().strftime('%Y-%m-%d %H:%M')}",
            f"- 会话模式：{'深度研究' if conversation.mode == 'agent' else '受控 RAG'}",
            f"- 知识库范围：{source_line}",
            f"- 模型：{conversation.provider} / {conversation.model}",
            "",
            "---",
            "",
            _message_markdown(message),
        ]
    )
    return {"ok": True, "path": _write_markdown("ingest", conversation.title, document)}


def _gateway_url(path: str) -> str:
    base = (settings.dsh_gateway_base or "").strip().rstrip("/")
    token = (settings.dsh_gateway_token or "").strip()
    if not base or not token:
        raise ConfigurationError("DSH 网关未配置。请在 SAG 环境中设置 SAG_DSH_GATEWAY_BASE 和 SAG_DSH_GATEWAY_TOKEN。")
    return f"{base}{path}"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.dsh_gateway_token}",
        "Content-Type": "application/json",
    }


def _delegation_secret() -> str:
    return (settings.dsh_delegation_secret or settings.secret_key).strip()


def _issue_delegation(*, user: User, session_id: str | None, source_ids: list[str]) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user.id,
            "aud": _DELEGATION_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(seconds=settings.dsh_delegation_ttl_seconds),
            "jti": uuid4().hex,
            "session_id": session_id or "",
            "source_ids": source_ids,
        },
        _delegation_secret(),
        algorithm="HS256",
    )


def _read_delegation(value: str | None) -> dict[str, Any]:
    if not value or not value.lower().startswith("bearer "):
        raise AuthError("缺少 DSH 检索委托")
    try:
        payload = jwt.decode(
            value[7:].strip(),
            _delegation_secret(),
            algorithms=["HS256"],
            audience=_DELEGATION_AUDIENCE,
        )
    except jwt.PyJWTError as error:
        raise AuthError("DSH 检索委托无效或已过期") from error
    if not isinstance(payload.get("source_ids"), list) or not isinstance(payload.get("sub"), str):
        raise AuthError("DSH 检索委托缺少作用域")
    return payload


async def _validate_source_scope(session: AsyncSession, source_ids: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(source_id.strip() for source_id in source_ids if source_id.strip()))
    if len(normalized) != len(source_ids):
        raise ValidationError("知识库范围包含空值或重复项")
    if not normalized:
        return []
    rows = (await session.scalars(select(Source.id).where(Source.id.in_(normalized)))).all()
    if len(rows) != len(normalized):
        raise ValidationError("选择的知识库不存在或已被删除")
    return normalized


async def _resolve_dsh_source_scope(
    session: AsyncSession,
    requested_ids: list[str],
    query: str,
) -> tuple[list[str], str, list[str]]:
    """Resolve selected sources and explicit ``@知识库`` mentions together.

    The DSH composer supports both choosing a source from the mention menu and
    pasting a completed ``@名称`` directly.  The latter used to leave
    ``source_ids`` empty, silently widening or losing the intended scope.
    """

    selected = await _validate_source_scope(session, requested_ids)
    rows = (await session.execute(select(Source.id, Source.name))).all()
    mentioned = [(source_id, name) for source_id, name in rows if name and f"@{name}".casefold() in query.casefold()]
    source_ids = list(dict.fromkeys([*selected, *(source_id for source_id, _ in mentioned)]))
    source_ids = await _validate_source_scope(session, source_ids)
    clean_query = query
    for _, name in mentioned:
        clean_query = re.sub(re.escape(f"@{name}"), " ", clean_query, flags=re.IGNORECASE)
    clean_query = re.sub(r"\s+", " ", clean_query).strip()
    return source_ids, clean_query or query.strip(), [name for _, name in mentioned]


async def _native_compatible_dsh_retrieval(
    session: AsyncSession,
    engine_manager: EngineManager,
    source_ids: list[str],
    query: str,
    top_k: int,
) -> dict[str, Any]:
    """DSH-only adapter for the native ``search_context`` retrieval contract.

    This intentionally invokes the same read-only tool used by native
    conversations.  It preserves DSH's independent state machine while
    carrying over event-aware evidence priority and missing-chunk hydration,
    which are essential for interface-parameter questions stored as events.
    """

    sources = await search_source_candidates(session, source_ids)
    await session.commit()
    limit = max(1, min(int(top_k), 20))
    result = await SearchContextTool().invoke(
        {"query": query, "top_k": limit},
        ToolContext(
            engine_manager=engine_manager,
            sources=sources,
            persona={"search_strategy": "vector", "top_k": limit},
        ),
    )
    sections = result.data.get("sections") if isinstance(result.data, dict) else []
    source_by_config = {source.sag_source_config_id: source for source in sources}
    documents = (
        list(
            await session.scalars(
                select(Document).where(
                    Document.source_id.in_([source.id for source in sources]),
                    Document.sag_source_id.is_not(None),
                )
            )
        )
        if sources
        else []
    )
    document_names = {
        (document.source_id, document.sag_source_id): document.filename
        for document in documents
        if document.sag_source_id
    }
    chunk_ids_by_config: dict[str, set[str]] = {}
    for section in sections if isinstance(sections, list) else []:
        source_config_id = str(getattr(section, "source_config_id", "") or "")
        chunk_id = str(getattr(section, "chunk_id", "") or "")
        if source_config_id and chunk_id:
            chunk_ids_by_config.setdefault(source_config_id, set()).add(chunk_id)

    # ``get_chunk`` deliberately exposes only reading fields to native UI
    # consumers. DSH needs the originating document ID solely to render its
    # citation label as "文章标题 - 知识库". Resolve that one-to-one mapping
    # from the same per-source engine store without changing native contracts.
    chunk_document_ids: dict[tuple[str, str], str] = {}
    factory = getattr(engine_manager, "_relational_session_factory", None)
    if callable(factory):
        try:
            from zleap.sag.db.models import SourceChunk

            for source_config_id, chunk_ids in chunk_ids_by_config.items():
                engine_session_factory = await factory(source_config_id)
                async with engine_session_factory() as engine_session:
                    rows = await engine_session.execute(
                        select(SourceChunk.id, SourceChunk.source_id).where(
                            SourceChunk.data_source_id == source_config_id,
                            SourceChunk.id.in_(chunk_ids),
                        )
                    )
                chunk_document_ids.update(
                    {
                        (source_config_id, str(chunk_id)): str(document_id)
                        for chunk_id, document_id in rows.all()
                        if chunk_id and document_id
                    }
                )
        except Exception:  # Citation labels degrade to heading, never retrieval itself.
            chunk_document_ids = {}
    payload_sections: list[dict[str, Any]] = []
    for section in sections if isinstance(sections, list) else []:
        source_config_id = str(getattr(section, "source_config_id", "") or "")
        source = source_by_config.get(source_config_id)
        document_id = str(getattr(section, "source_id", "") or "") or chunk_document_ids.get(
            (source_config_id, str(getattr(section, "chunk_id", "") or "")), ""
        )
        payload_sections.append(
            {
                "chunk_id": getattr(section, "chunk_id", None),
                "heading": str(getattr(section, "heading", "") or "片段"),
                "content": str(getattr(section, "content", "") or ""),
                "score": float(getattr(section, "score", 0.0) or 0.0),
                "rank": int(getattr(section, "rank", 0) or 0),
                "source_id": source.id if source else None,
                "source_name": source.name if source else None,
                "document_name": document_names.get((source.id, document_id)) if source and document_id else None,
            }
        )
    stats = {
        "sources": len(sources),
        "candidate_count": int(result.data.get("candidate_count") or len(payload_sections)),
        "event_candidates": int(result.data.get("event_candidates") or 0),
        "event_hits": int(result.data.get("event_count") or 0),
        "event_recall": "native_search_context",
        "section_count": len(payload_sections),
    }
    return {"query": query, "sections": payload_sections, "events": [], "source_hits": [], "stats": stats}


async def _history_for_gateway(session: AsyncSession, session_id: str, query: str) -> list[dict[str, str]]:
    rows = list(
        await session.scalars(
            select(DshMessage)
            .where(DshMessage.session_id == session_id)
            .order_by(DshMessage.created_at.desc())
            .limit(13)
        )
    )
    rows.reverse()
    values = [{"role": row.role, "content": row.content} for row in rows if row.content]
    if values and values[-1]["role"] == "user" and values[-1]["content"] == query:
        values.pop()
    return values[-12:]


def _image_payload(attachments: list[DshAttachmentInput]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for item in attachments:
        path = attachment_path(item.id)
        if path is None:
            raise ValidationError("会话图片附件不存在或不可用")
        suffix = path[path.rfind(".") :].lower()
        media_type = _IMAGE_TYPES.get(suffix)
        if media_type is None or (item.media_type and item.media_type != media_type):
            raise ValidationError("会话图片附件格式无效")
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        images.append({"data": encoded, "media_type": media_type, "name": item.name or item.id})
    return images


@router.get("/capabilities")
async def capabilities(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Expose the DSH model and permission catalog without gateway credentials."""

    try:
        async with httpx.AsyncClient(timeout=settings.dsh_gateway_timeout_seconds) as client:
            response = await client.get(_gateway_url("/capabilities"), headers=_headers())
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as error:
        raise UpstreamError(f"DSH 网关能力目录不可用: {error}") from error
    return payload if isinstance(payload, dict) else {"groups": [], "permission_presets": []}


async def _proxy_stream(
    url: str, payload: dict[str, Any], request: Request | None = None
) -> AsyncIterator[dict[str, str]]:
    timeout = httpx.Timeout(settings.dsh_gateway_timeout_seconds, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=_headers(), json=payload) as response:
                response.raise_for_status()
                event = "message"
                data: list[str] = []
                async for line in response.aiter_lines():
                    if request is not None and await request.is_disconnected():
                        break
                    if line.startswith("event:"):
                        event = line[6:].strip() or "message"
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line:
                        if data:
                            raw = "\n".join(data)
                            try:
                                normalized = json.dumps(json.loads(raw), ensure_ascii=False)
                            except json.JSONDecodeError:
                                normalized = json.dumps(
                                    {"type": "run.failed", "error": "DSH 网关返回无效流事件"}, ensure_ascii=False
                                )
                                event = "run.failed"
                            yield {"event": event, "data": normalized}
                        event, data = "message", []
                if data:
                    yield {"event": event, "data": "\n".join(data)}
    except httpx.HTTPError as error:
        yield {
            "event": "run.failed",
            "data": json.dumps({"type": "run.failed", "error": f"DSH 网关不可用: {error}"}, ensure_ascii=False),
        }


@router.post("/stream")
async def stream(
    body: DshStreamRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    """Start a DSH turn with scoped retrieval, bounded history, and images."""

    conversation: DshSession | None = None
    if body.session_id:
        conversation = await _owned_session(session, user, body.session_id)
    if conversation:
        source_ids, clean_query, mentioned_names = await _resolve_dsh_source_scope(session, body.source_ids, body.query)
    else:
        source_ids = list(dict.fromkeys(source_id.strip() for source_id in body.source_ids if source_id.strip()))
        clean_query, mentioned_names = body.query.strip(), []
    history = await _history_for_gateway(session, conversation.id, body.query) if conversation else []
    if conversation:
        source_names = (
            list(await session.scalars(select(Source.name).where(Source.id.in_(source_ids))))
            if source_ids
            else mentioned_names
        )
        conversation.source_ids = source_ids
        conversation.provider = body.provider
        conversation.model = body.model
        conversation.permission_preset = body.permission_preset
        conversation.mode = body.mode
        conversation.web_enabled = body.web_enabled
        if conversation.title == "新 DSH 会话":
            conversation.title = _dsh_title_from_query(clean_query, source_names)
        await session.commit()
    endpoint = "/agent" if body.mode == "agent" else "/generate"
    payload: dict[str, Any] = {
        "session_id": conversation.id if conversation else "",
        "query": clean_query,
        "history": history,
        "provider": body.provider,
        "model": body.model,
        "source_ids": source_ids,
        "permission_preset": body.permission_preset,
        "web_enabled": body.web_enabled,
        "delegation_token": _issue_delegation(user=user, session_id=body.session_id, source_ids=source_ids),
        "images": _image_payload(body.attachments),
    }
    if body.reasoning_effort:
        payload["reasoning_effort"] = body.reasoning_effort
    return EventSourceResponse(_proxy_stream(_gateway_url(endpoint), payload, request))


@router.post("/retrieval")
async def delegated_retrieval(
    body: DshRetrievalRequest,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> dict[str, Any]:
    """Retrieval callback usable only by the DSH gateway's short-lived delegation."""

    claims = _read_delegation(authorization)
    scoped_ids = [value for value in claims["source_ids"] if isinstance(value, str) and value]
    if body.source_ids and set(body.source_ids) != set(scoped_ids):
        raise ForbiddenError("DSH 请求的知识库范围超出当前会话授权")
    return await _native_compatible_dsh_retrieval(
        session,
        engine_manager,
        scoped_ids,
        body.query,
        body.top_k,
    )
