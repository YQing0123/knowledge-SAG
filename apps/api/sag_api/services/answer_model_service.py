from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.errors import ConflictError, NotFoundError, ValidationError
from sag_api.db.models import AnswerModelProfile, User
from sag_api.generation import LLMClient
from sag_api.services import settings_service

SUPPORTED_PROTOCOLS = {"openai-compatible", "openai-responses"}


def normalize_base_url(value: str) -> str:
    """Normalize a provider root for LiteLLM's OpenAI-compatible transport."""
    base = value.strip().rstrip("/")
    if not base:
        return base
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(base)
    if parsed.path.endswith("/chat/completions"):
        base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path[: -len("/chat/completions")], "", ""))
    elif parsed.path.endswith("/responses"):
        base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path[: -len("/responses")], "", ""))
        parsed = urlsplit(base)
    if parsed.scheme in {"http", "https"} and not parsed.path:
        base = urlunsplit((parsed.scheme, parsed.netloc, "/v1", "", ""))
    return base.rstrip("/")


def _clean_model(value: dict[str, Any]) -> dict[str, Any]:
    model_id = str(value.get("id") or value.get("model_id") or "").strip()
    if not model_id:
        raise ValidationError("模型 ID 不能为空")
    display_name = str(value.get("display_name") or value.get("name") or model_id).strip()
    context_window = int(value.get("context_window") or 128_000)
    max_output_tokens = int(value.get("max_output_tokens") or 8_192)
    if context_window < 1024 or context_window > 2_000_000:
        raise ValidationError("上下文窗口必须在 1024 到 2000000 之间")
    if max_output_tokens < 1 or max_output_tokens > 1_000_000:
        raise ValidationError("最大输出 token 必须在 1 到 1000000 之间")
    return {
        "id": model_id,
        "display_name": display_name[:200],
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
    }


def normalize_models(models: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in models or []:
        item = _clean_model(raw)
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        result.append(item)
    return result


def profile_out(profile: AnswerModelProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "provider_id": profile.provider_id,
        "display_name": profile.display_name,
        # Profiles created before URL normalization may still contain the
        # provider root. Return the effective LiteLLM endpoint to the UI.
        "base_url": normalize_base_url(profile.base_url),
        "protocol": profile.protocol,
        "api_key_set": bool(profile.api_key),
        "models": profile.models or [],
        "is_default": bool(profile.is_default),
        "enabled": bool(profile.enabled),
        "created_at": profile.created_at.isoformat(),
        "updated_at": profile.updated_at.isoformat(),
    }


async def ensure_default_profile(session: AsyncSession, user: User) -> None:
    existing = await session.scalar(
        select(AnswerModelProfile).where(AnswerModelProfile.user_id == user.id).limit(1)
    )
    if existing is not None:
        # A profile may have been auto-created before the legacy model form was
        # completed. Backfill its secret once the global config becomes ready.
        if existing.is_default and not existing.api_key and settings.llm_api_key:
            existing.api_key = settings.llm_api_key
            if not existing.base_url:
                existing.base_url = normalize_base_url(str(settings.llm_base_url or ""))
            if not existing.models:
                existing.models = [{
                    "id": settings.llm_model,
                    "display_name": settings.llm_model,
                    "context_window": settings.llm_context_window,
                    "max_output_tokens": settings.llm_max_tokens,
                }]
            await session.commit()
        return
    config = settings_service.effective_model_config()
    profile = AnswerModelProfile(
        user_id=user.id,
        provider_id=str(config["llm_provider"]),
        display_name="默认回答模型",
        base_url=normalize_base_url(str(config.get("llm_base_url") or "")),
        protocol="openai-compatible",
        api_key=settings.llm_api_key,
        models=[
            {
                "id": str(config["llm_model"]),
                "display_name": str(config["llm_model"]),
                "context_window": int(config.get("llm_context_window") or 128_000),
                "max_output_tokens": int(config.get("llm_max_tokens") or 4096),
            }
        ],
        is_default=True,
        enabled=True,
    )
    session.add(profile)
    await session.commit()


async def list_profiles(session: AsyncSession, user: User) -> list[dict[str, Any]]:
    await ensure_default_profile(session, user)
    rows = (
        await session.execute(
            select(AnswerModelProfile)
            .where(AnswerModelProfile.user_id == user.id)
            .order_by(AnswerModelProfile.is_default.desc(), AnswerModelProfile.created_at)
        )
    ).scalars().all()
    return [profile_out(row) for row in rows]


async def get_profile(session: AsyncSession, user: User, profile_id: str) -> AnswerModelProfile:
    row = await session.scalar(
        select(AnswerModelProfile).where(
            AnswerModelProfile.id == profile_id,
            AnswerModelProfile.user_id == user.id,
        )
    )
    if row is None:
        raise NotFoundError("回答模型提供方不存在")
    return row


async def resolve_profile(
    session: AsyncSession,
    user: User,
    profile_id: str | None,
    model_id: str | None,
) -> tuple[AnswerModelProfile, dict[str, Any]]:
    await ensure_default_profile(session, user)
    if profile_id:
        profile = await get_profile(session, user, profile_id)
    else:
        profile = await session.scalar(
            select(AnswerModelProfile)
            .where(AnswerModelProfile.user_id == user.id, AnswerModelProfile.is_default.is_(True))
            .limit(1)
        )
        if profile is None:
            profile = await session.scalar(
                select(AnswerModelProfile).where(AnswerModelProfile.user_id == user.id).limit(1)
            )
    if profile is None or not profile.enabled:
        raise ValidationError("没有可用的回答模型提供方")
    models = normalize_models(profile.models or [])
    wanted = model_id.strip() if model_id else ""
    selected = next((item for item in models if item["id"] == wanted), None) if wanted else None
    if selected is None and models:
        selected = models[0]
    if selected is None:
        raise ValidationError("该提供方尚未配置模型")
    return profile, selected


def build_llm_client(profile: AnswerModelProfile, model: dict[str, Any]) -> LLMClient:
    if not profile.api_key:
        raise ValidationError("该回答模型提供方尚未配置 API Key")
    # Chat-compatible profiles use LiteLLM; Responses profiles are dispatched
    # by LLMClient through the native /responses transport.
    runtime_settings = settings.model_copy(
        update={
            "llm_provider": "openai",
            "llm_base_url": normalize_base_url(profile.base_url),
            "llm_api_key": profile.api_key,
            "llm_model": model["id"],
            "llm_context_window": model["context_window"],
            "llm_max_tokens": model["max_output_tokens"],
        }
    )
    return LLMClient(runtime_settings, protocol=profile.protocol)


async def create_profile(session: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(payload.get("provider_id") or "").strip().lower()
    display_name = str(payload.get("display_name") or "").strip()
    base_url = normalize_base_url(str(payload.get("base_url") or ""))
    protocol = str(payload.get("protocol") or "openai-compatible").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not provider_id or not display_name or not base_url:
        raise ValidationError("提供方 ID、显示名称和 API 地址不能为空")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValidationError("仅支持 openai-compatible 和 openai-responses 协议")
    if not api_key:
        raise ValidationError("API Key 不能为空")
    duplicate = await session.scalar(
        select(AnswerModelProfile).where(
            AnswerModelProfile.user_id == user.id,
            AnswerModelProfile.provider_id == provider_id,
        )
    )
    if duplicate is not None:
        raise ConflictError("Provider ID 已存在，请编辑现有提供方")
    models = normalize_models(payload.get("models"))
    row = AnswerModelProfile(
        user_id=user.id,
        provider_id=provider_id,
        display_name=display_name[:200],
        base_url=base_url[:500],
        protocol=protocol,
        api_key=api_key,
        models=models,
        is_default=bool(payload.get("is_default", False)),
        enabled=True,
    )
    if row.is_default:
        await session.execute(
            update(AnswerModelProfile)
            .where(AnswerModelProfile.user_id == user.id)
            .values(is_default=False)
        )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return profile_out(row)


async def update_profile(session: AsyncSession, user: User, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = await get_profile(session, user, profile_id)
    if payload.get("provider_id"):
        duplicate = await session.scalar(
            select(AnswerModelProfile).where(
                AnswerModelProfile.user_id == user.id,
                AnswerModelProfile.provider_id == str(payload["provider_id"]).strip().lower(),
                AnswerModelProfile.id != row.id,
            )
        )
        if duplicate is not None:
            raise ConflictError("Provider ID 已存在，请使用其他 ID")
    for key in ("provider_id", "display_name", "base_url", "protocol"):
        if key in payload and payload[key] is not None:
            value = str(payload[key]).strip()
            if key == "provider_id":
                value = value.lower()
            if key == "protocol" and value not in SUPPORTED_PROTOCOLS:
                raise ValidationError("仅支持 openai-compatible 和 openai-responses 协议")
            if key == "base_url":
                value = normalize_base_url(value)
            setattr(row, key, value)
    if "api_key" in payload and payload["api_key"]:
        row.api_key = str(payload["api_key"]).strip()
    if "models" in payload:
        row.models = normalize_models(payload.get("models"))
    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    if payload.get("is_default"):
        await session.execute(
            update(AnswerModelProfile)
            .where(AnswerModelProfile.user_id == user.id, AnswerModelProfile.id != row.id)
            .values(is_default=False)
        )
        row.is_default = True
    await session.commit()
    await session.refresh(row)
    return profile_out(row)
