from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import ApiError, ConflictError, UpstreamError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import model_provider_catalog
from sag_api.db.models import AnswerModelProfile, Source, User
from sag_api.generation import LLMClient
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.sag.engine_manager import EngineManager
from sag_api.schemas.system import (
    AnswerModelDiscoverRequest,
    AnswerModelProfileCreate,
    AnswerModelProfileUpdate,
    DshIntegrationUpdate,
    McpApiKeyCreate,
    ModelConfigUpdate,
    QuickModelSetupRequest,
    SystemPreferencesUpdate,
)
from sag_api.services import answer_model_service, mcp_api_key_service, settings_service

router = APIRouter(prefix="/system", tags=["system"])
log = get_logger("system")


async def _discover_model_catalog(base_url: str, api_key: str) -> dict[str, object]:
    """Probe standard and root model directories without persisting secrets."""
    normalized = answer_model_service.normalize_base_url(base_url)
    candidates = [normalized]
    raw_root = base_url.strip().rstrip("/")
    if raw_root and raw_root not in candidates:
        candidates.append(raw_root)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    candidate + "/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            continue
        raw_items = payload.get("data", []) if isinstance(payload, dict) else []
        models: list[dict[str, object]] = []
        for item in raw_items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            models.append(
                {
                    "id": str(item["id"]),
                    "display_name": str(item.get("name") or item["id"]),
                    "context_window": int(item.get("context_window") or 128_000),
                    "max_output_tokens": int(item.get("max_output_tokens") or 8_192),
                }
            )
        if models or len(candidates) == 1:
            clean = answer_model_service.normalize_models(models)
            return {"models": clean, "count": len(clean), "base_url": candidate}
    raise UpstreamError(f"获取模型目录失败：{last_error or '返回格式不正确'}")


def _capabilities() -> dict:
    strategy_report = EngineManager.strategies_capability_report(settings)
    return {
        "llm_configured": settings.llm_configured,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "context_window": settings.llm_context_window,
        "embedding_model": settings.embedding_model,
        "document_parser": settings.document_parser,
        "effective_document_parser": settings.effective_document_parser,
        "mineru_configured": settings.mineru_configured,
        "vector_provider": settings.sag_vector_provider,
        "language": settings.sag_language,
        "search_strategy": settings.search_strategy,
        "search_strategies": strategy_report["enabled"],
        "search_strategies_disabled": strategy_report["disabled"],
        "timezone": settings.timezone,
        "max_upload_mb": settings.max_upload_mb,
        "allowed_upload_exts": sorted(settings.allowed_upload_exts),
        "dsh_enabled": settings.dsh_enabled,
    }


@router.get("/health")
async def health() -> dict:
    """存活探针：进程在跑即 200（不触碰依赖）。"""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """就绪探针：数据库与知识运行时均就绪才返回 200。"""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.warning("就绪检查失败：%s", e)
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": False})

    runtime = getattr(request.app.state, "knowledge_runtime", None)
    coordinator = getattr(request.app.state, "storage_bootstrap", None)  # [storage-bootstrap]
    if runtime is None or not runtime.ready:
        content: dict[str, object] = {"status": "unavailable", "db": True}
        if coordinator is not None:
            content["phase"] = coordinator.public_status()["phase"]
        return JSONResponse(status_code=503, content=content)
    return JSONResponse(content={"status": "ready", "db": True})


@router.get("/capabilities")
async def capabilities() -> dict:
    """能力探测：供前端判断是否已配置 LLM、当前引擎后端等。"""
    return _capabilities()


@router.get("/model-config")
async def get_model_config(
    _user: User = Depends(get_current_user),
) -> dict:
    """当前生效的模型与检索配置（密钥脱敏为 *_set 布尔）。"""
    return settings_service.effective_model_config()


@router.get("/model-providers")
async def get_model_providers(
    _user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """前后端共享的模型接入能力与技术默认值。"""
    return model_provider_catalog()


@router.get("/answer-models")
async def get_answer_model_profiles(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """列出回答模型提供方；密钥只返回是否已配置。"""
    return await answer_model_service.list_profiles(session, _user)


@router.post("/answer-models")
async def create_answer_model_profile(
    body: AnswerModelProfileCreate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await answer_model_service.create_profile(session, _user, body.model_dump())


@router.patch("/answer-models/{profile_id}")
async def update_answer_model_profile(
    profile_id: str,
    body: AnswerModelProfileUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await answer_model_service.update_profile(
        session,
        _user,
        profile_id,
        body.model_dump(exclude_unset=True),
    )


@router.delete("/answer-models/{profile_id}")
async def delete_answer_model_profile(
    profile_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    row = await answer_model_service.get_profile(session, _user, profile_id)
    was_default = row.is_default
    await session.delete(row)
    if was_default:
        replacement = await session.scalar(
            select(AnswerModelProfile)
            .where(
                AnswerModelProfile.user_id == _user.id,
                AnswerModelProfile.id != profile_id,
                AnswerModelProfile.enabled.is_(True),
            )
            .order_by(AnswerModelProfile.created_at)
            .limit(1)
        )
        if replacement is not None:
            replacement.is_default = True
    await session.commit()
    return {"ok": True}


@router.post("/answer-models/discover")
async def discover_answer_models_preview(
    body: AnswerModelDiscoverRequest,
    _user: User = Depends(get_current_user),
) -> dict[str, object]:
    """在新增提供方尚未保存时，也允许即时查看模型目录。"""
    return await _discover_model_catalog(body.base_url, body.api_key)


@router.post("/answer-models/{profile_id}/discover")
async def discover_answer_models(
    profile_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """调用提供方的 OpenAI /models 目录，返回可直接保存的模型项。"""
    profile = await answer_model_service.get_profile(session, _user, profile_id)
    if not profile.api_key:
        raise ValidationError("该提供方尚未配置 API Key")
    return await _discover_model_catalog(profile.base_url, profile.api_key)


@router.get("/preferences")
async def get_system_preferences(
    _user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Presentation preferences shared by this local-first installation."""
    return settings_service.effective_system_preferences()


@router.put("/preferences")
async def update_system_preferences(
    body: SystemPreferencesUpdate,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    preferences = await settings_service.save_system_preferences(
        session,
        body.model_dump(exclude_unset=True),
    )
    runtime = getattr(request.app.state, "knowledge_runtime", None)
    if runtime is not None:
        await runtime.apply_settings(settings, reset_engines=False, wait_for_reset=False)
    return preferences


@router.get("/dsh-integration")
async def get_dsh_integration(
    _user: User = Depends(get_current_user),
) -> dict[str, object]:
    """Read public DSH integration state; the gateway token is never returned."""
    return settings_service.effective_dsh_integration_config()


@router.put("/dsh-integration")
async def update_dsh_integration(
    body: DshIntegrationUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    config = await settings_service.save_dsh_integration_config(
        session,
        body.model_dump(exclude_unset=True),
    )
    return {"config": config, "capabilities": _capabilities()}


@router.get("/model-setup")
async def get_model_setup_status(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """首次进入时判断是否需要展示快捷模型配置。"""
    return await settings_service.model_setup_status(session)


@router.get("/mcp")
async def knowledge_mcp_descriptor(
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """返回将整个 SAG 知识库挂入外部 MCP 宿主的连接信息。"""
    source_count = await session.scalar(select(func.count(Source.id))) or 0
    base = str(request.base_url).rstrip("/")
    return {
        "name": "SAG 知识库",
        "scope": "knowledge_base",
        "source_count": source_count,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/",
            "headers": {"Authorization": "Bearer <MCP_API_KEY>"},
            "auth": mcp_api_key_service.public_descriptor_auth(),
            "note": (
                "默认开放全部信源；Dify 等宿主请使用 streamable_http/Streamable HTTP 传输，"
                "在 Authorization 头携带设置页生成的 Bearer sag_mcp_<generated-key>，"
                "也可在 URL 添加 ?source_id=<id> 临时限定单个信源。"
            ),
        },
        "stdio": {
            "command": "python",
            "args": ["-m", "sag_api.mcp.server"],
            "env": {},
            "note": "默认开放全部信源；设置 SAG_MCP_SOURCE_ID 可限定单个信源。",
        },
    }


@router.get("/mcp/api-keys")
async def list_mcp_api_keys(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """列出当前用户的 MCP Key 元数据；不会返回密钥本身。"""
    return await mcp_api_key_service.list_keys(session, _user)


@router.post("/mcp/api-keys")
async def create_mcp_api_key(
    body: McpApiKeyCreate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """创建 MCP Key；raw key 仅在本次响应返回一次。"""
    metadata, raw_key = await mcp_api_key_service.create_key(session, _user, body.name)
    return {"key": raw_key, **metadata}


@router.post("/mcp/api-keys/{key_id}/revoke")
async def revoke_mcp_api_key(
    key_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    return await mcp_api_key_service.revoke_key(session, _user, key_id)


@router.post("/model-setup/302")
async def quick_setup_302(
    body: QuickModelSetupRequest,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """只接收一个 302.AI Key，写入生成、向量、MinerU 与检索预设。"""
    status = await settings_service.model_setup_status(session)
    if not status["required"]:
        raise ConflictError("模型配置已存在，请在设置中修改")

    config = await settings_service.save_302_quick_setup(session, body.api_key)
    await request.app.state.knowledge_runtime.apply_settings(
        settings,
        reset_engines=True,
        wait_for_reset=False,
    )
    return {"config": config, "capabilities": _capabilities()}


@router.put("/model-config")
async def update_model_config(
    body: ModelConfigUpdate,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存运行期配置；仅在模型/向量配置实际变化时安全重建引擎。"""
    patch = body.model_dump(exclude_unset=True)
    before = settings_service.effective_model_config()
    config = await settings_service.save_model_config(session, patch)

    # 解析器/检索参数保存无需打断暖引擎；只有引擎配置真的变化才安全重建。
    engine_fields = {
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_timeout_ms",
        "llm_max_retries",
        "llm_structured_output_mode",
        "embedding_model",
        "embedding_base_url",
        "embedding_dimensions",
        "sag_language",
    }
    engine_changed = any(before.get(key) != config.get(key) for key in engine_fields)
    engine_changed = engine_changed or bool(patch.get("llm_api_key") or patch.get("embedding_api_key"))
    await request.app.state.knowledge_runtime.apply_settings(
        settings,
        reset_engines=engine_changed,
        wait_for_reset=False,
    )
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/mineru/302")
async def configure_302_mineru(
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """已有 302 LLM/Embedding 用户一键复用服务端保存的 Key 启用 MinerU。"""
    config = await settings_service.save_302_mineru_setup(session)
    await request.app.state.knowledge_runtime.apply_settings(
        settings,
        reset_engines=False,
        wait_for_reset=False,
    )
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/test")
async def test_model_config(
    request: Request,
    body: ModelConfigUpdate | None = None,
    _user: User = Depends(get_current_user),
) -> dict:
    """连接测试：优先验证表单草稿，不持久化也不修改运行期单例。"""
    llm: LLMClient
    active = settings
    if body is None:
        llm = request.app.state.llm
    else:
        patch = body.model_dump(exclude_unset=True)
        updates = {
            key: (None if key in {"llm_base_url"} and value == "" else value)
            for key, value in patch.items()
            if not (key == "llm_api_key" and not value)
        }
        active = settings.model_copy(update=updates)
        llm = LLMClient(active)
    if not llm.configured:
        return {"ok": False, "message": "尚未配置 API Key"}
    try:
        await llm.complete([{"role": "user", "content": "ping"}])
        return {
            "ok": True,
            "message": f"连接成功 · {active.llm_provider} / {active.llm_model}",
        }
    except ApiError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
