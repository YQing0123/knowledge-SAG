"""运行期模型与知识库配置 —— DB 覆盖层叠加在 env 默认（`settings` 单例）之上。

单用户本地示范：把「模型与检索」配置存进 `settings` 表（scope=global, key=model_config）。
启动时与保存后**就地覆盖 `settings` 单例**的相应字段，端点再重建 `LLMClient` / 重置暖引擎，
使配置改动**无需重启即生效**。api_key 明文入库（本地单用户可接受），读取时脱敏（只返回是否已设）。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.core.config import Settings
from sag_api.core.config import settings as _settings
from sag_api.core.errors import ConfigurationError, ConflictError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import get_model_provider
from sag_api.db.models import Document, Setting
from sag_api.enums import SEARCH_STRATEGIES, normalize_search_strategy

_SCOPE = "global"
_KEY = "model_config"
_PREFERENCES_KEY = "system_preferences"
_DSH_INTEGRATION_KEY = "dsh_integration"
_DEFAULT_CONVERSATION_ARCHIVE_DIR = "知识层/会话归档"
_DEFAULT_CONVERSATION_INGEST_DIR = "知识层/会话入库"
_DEFAULT_PROCESSING_LOG_RETENTION_DAYS = 30
_processing_log_retention_days = _DEFAULT_PROCESSING_LOG_RETENTION_DAYS
log = get_logger("settings")

# 允许运行期覆盖的字段（值已由请求 schema 校验/转型）
_FIELDS = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_context_window",
        "llm_timeout_ms",
        "llm_max_retries",
        "llm_structured_output_mode",
        "embedding_model",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_dimensions",
        "document_parser",
        "mineru_provider",
        "mineru_base_url",
        "mineru_api_key",
        "mineru_version",
        "mineru_official_model",
        "document_extract_concurrency",
        "document_chunk_max_tokens",
        "document_chunk_mode",
        "search_strategy",
        "search_top_k",
        "sag_language",
    }
)
_SECRET_FIELDS = frozenset({"llm_api_key", "embedding_api_key", "mineru_api_key"})
_NULLABLE_FIELDS = frozenset({"llm_base_url", "embedding_base_url", "embedding_dimensions", "mineru_base_url"})
_LOCKABLE_LLM_FIELDS = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_context_window",
        "llm_timeout_ms",
        "llm_max_retries",
        "llm_structured_output_mode",
    }
)
_MODEL_CONFIG_SOURCES = {field: "default" for field in _FIELDS - _SECRET_FIELDS}

_OPENAI_COMPATIBLE = get_model_provider("openai")

QUICK_SETUP_302 = {
    "llm_provider": _OPENAI_COMPATIBLE.id,
    "llm_base_url": _OPENAI_COMPATIBLE.default_base_url,
    "llm_model": _OPENAI_COMPATIBLE.default_model,
    "llm_temperature": _OPENAI_COMPATIBLE.default_temperature,
    "llm_max_tokens": 4_096,
    "llm_context_window": _OPENAI_COMPATIBLE.default_context_window,
    "llm_timeout_ms": 180_000,
    "llm_max_retries": 2,
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "embedding_base_url": "https://api.302ai.cn/v1",
    "embedding_dimensions": 1024,
    "document_parser": "auto",
    "mineru_provider": "302",
    "mineru_base_url": "https://api.302ai.cn",
    "mineru_version": "2.5",
    "document_extract_concurrency": 4,
    "document_chunk_max_tokens": 1_000,
    "document_chunk_mode": "standard",
    "search_strategy": "multi_es_fast",
    "search_top_k": 8,
    "sag_language": "zh",
}

_LEGACY_302_BASE_URLS = {
    "https://api.302.ai": "https://api.302ai.cn",
    "https://api.302.ai/v1": "https://api.302ai.cn/v1",
}
_OFFICIAL_MINERU_BASE_URL = "https://mineru.net/api/v4"


async def _load_row(session: AsyncSession, key: str = _KEY) -> Setting | None:
    return await session.scalar(select(Setting).where(Setting.scope == _SCOPE, Setting.key == key))


def _embedding_identity(settings: Settings, patch: dict) -> tuple[str, int]:
    model = str(patch.get("embedding_model", settings.embedding_model))
    dimensions = patch.get("embedding_dimensions", settings.embedding_dimensions)
    return model, int(dimensions or 1024)


async def _reject_incompatible_embedding_change(
    session: AsyncSession,
    patch: dict,
) -> None:
    if _embedding_identity(_settings, patch) == _embedding_identity(_settings, {}):
        return
    indexed_document_id = await session.scalar(
        select(Document.id)
        .where(
            Document.is_active.is_(True),
            Document.sag_source_id.is_not(None),
        )
        .limit(1)
    )
    if indexed_document_id is not None:
        raise ConflictError(
            "当前知识库已有入库文档，更换 Embedding 模型或维度会导致现有向量失效；"
            "请先清理并重新入库文档，或等待向量重建功能。"
        )


def _normalize_overrides(overrides: dict) -> dict:
    """清理持久化配置，确保已下线或非法策略不会进入运行时。"""
    normalized = dict(overrides)
    for field in ("llm_base_url", "embedding_base_url", "mineru_base_url"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = _LEGACY_302_BASE_URLS.get(value.rstrip("/"), value)
    if normalized.get("mineru_provider") is None:
        mineru_url = str(normalized.get("mineru_base_url") or "")
        mineru_host = (urlparse(mineru_url).hostname or "").lower()
        normalized["mineru_provider"] = (
            "official" if mineru_host == "mineru.net" else "302"
        )
    elif (
        normalized["mineru_provider"] == "official"
        and str(normalized.get("mineru_base_url") or "").rstrip("/")
        == "https://api.302ai.cn"
    ):
        normalized["mineru_base_url"] = _OFFICIAL_MINERU_BASE_URL
    elif (
        normalized["mineru_provider"] == "302"
        and (urlparse(str(normalized.get("mineru_base_url") or "")).hostname or "").lower()
        == "mineru.net"
    ):
        # 用户在设置页只粘贴了官方 URL、未切换服务商下拉框时，协议必须跟随
        # URL 主机走官方分支，否则会把官方 v4 请求误发到 302 适配器。
        normalized["mineru_provider"] = "official"
    strategy = normalized.get("search_strategy")
    if strategy == "atomic":
        normalized["search_strategy"] = normalize_search_strategy(strategy)
        log.warning("旧检索策略 atomic 已迁移为精确模式 multi")
    elif strategy is not None and strategy not in SEARCH_STRATEGIES:
        normalized.pop("search_strategy", None)
        log.warning("忽略非法的持久化检索策略：%s", strategy)
    return normalized


async def load_overrides(session: AsyncSession) -> dict:
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return _normalize_overrides(raw)


async def model_setup_status(session: AsyncSession) -> dict[str, bool]:
    """判断是否需要首次模型配置，不受运行期 DB 覆盖后的 settings 单例干扰。"""
    row = await _load_row(session)
    environment_configured = Settings().llm_configured
    database_configured = bool(row and isinstance(row.value, dict) and row.value.get("llm_api_key"))
    return {
        "required": not environment_configured and not database_configured,
        "environment_configured": environment_configured,
        "database_configured": database_configured,
    }


def apply_overrides(settings: Settings, overrides: dict) -> None:
    """把持久化配置写回 settings；部署锁定时保留 LLM 环境值。"""
    normalized = _normalize_overrides(overrides)
    locked_fields = _LOCKABLE_LLM_FIELDS if settings.lock_llm_config else frozenset()
    for key, value in normalized.items():
        if key in _FIELDS and key not in locked_fields:
            setattr(settings, key, value)
    if settings is _settings:
        for key in _MODEL_CONFIG_SOURCES:
            if key in locked_fields:
                _MODEL_CONFIG_SOURCES[key] = "environment_policy"
            elif key in normalized:
                _MODEL_CONFIG_SOURCES[key] = "database"


async def apply_startup_overrides(session_factory: async_sessionmaker) -> None:
    """启动时：把 DB 里的模型配置覆盖到 settings 单例（在构建 LLMClient 之前调用）。"""
    async with session_factory() as session:
        row = await _load_row(session)
        raw = dict(row.value) if row and isinstance(row.value, dict) else {}
        overrides = _normalize_overrides(raw)
        if row is not None and overrides != raw:
            # JSON 列未使用 MutableDict，必须整体重新赋值才能可靠持久化。
            row.value = overrides
            await session.commit()
        apply_overrides(_settings, overrides)
        global _processing_log_retention_days
        preferences = await _load_row(session, _PREFERENCES_KEY)
        preference_values = dict(preferences.value) if preferences and isinstance(preferences.value, dict) else {}
        try:
            _processing_log_retention_days = max(
                1,
                min(3650, int(preference_values.get("processing_log_retention_days"))),
            )
        except (TypeError, ValueError):
            _processing_log_retention_days = _DEFAULT_PROCESSING_LOG_RETENTION_DAYS
        timezone = preference_values.get("timezone")
        if isinstance(timezone, str):
            # Stored values were validated on write. Settings assignment is kept
            # explicit so model configuration and presentation preferences remain separate.
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("忽略非法的持久化时区：%s", timezone)
            else:
                _settings.timezone = timezone
        persisted_root = preference_values.get("root_dir")
        if isinstance(persisted_root, str) and persisted_root.strip():
            root = Path(persisted_root).expanduser().resolve()
            if root.is_dir():
                _settings.folder_sync_root = str(root)
                _settings.conversation_export_root = str(root)
                _settings.conversation_host_root = str(root)
            elif str(_settings.conversation_host_root or "").strip() != str(root):
                log.warning("忽略不可访问的持久化根目录：%s", persisted_root)
        for field, default in (
            ("conversation_archive_dir", _DEFAULT_CONVERSATION_ARCHIVE_DIR),
            ("conversation_ingest_dir", _DEFAULT_CONVERSATION_INGEST_DIR),
        ):
            try:
                setattr(_settings, field, _conversation_relative(preference_values.get(field), default))
            except ConfigurationError as error:
                log.warning("忽略非法的持久化会话目录 %s: %s", field, error)
        dsh = await _load_row(session, _DSH_INTEGRATION_KEY)
        _apply_dsh_integration_overrides(
            dict(dsh.value) if dsh and isinstance(dsh.value, dict) else {}
        )


def effective_model_config() -> dict:
    """当前生效的模型配置（读 settings 单例；密钥脱敏为 *_set 布尔）。"""
    return {
        "llm_provider": _settings.llm_provider,
        "llm_base_url": _settings.llm_base_url,
        "llm_model": _settings.llm_model,
        "llm_temperature": _settings.llm_temperature,
        "llm_max_tokens": _settings.llm_max_tokens,
        "llm_context_window": _settings.llm_context_window,
        "llm_timeout_ms": _settings.llm_timeout_ms,
        "llm_max_retries": _settings.llm_max_retries,
        "llm_structured_output_mode": _settings.llm_structured_output_mode,
        "llm_api_key_set": bool(_settings.llm_api_key),
        "embedding_model": _settings.embedding_model,
        "embedding_base_url": _settings.embedding_base_url,
        "embedding_dimensions": _settings.embedding_dimensions,
        "embedding_api_key_set": bool(_settings.embedding_api_key),
        "document_parser": _settings.document_parser,
        "effective_document_parser": _settings.effective_document_parser,
        "mineru_provider": _settings.mineru_provider,
        "mineru_base_url": _settings.mineru_base_url,
        "mineru_version": _settings.mineru_version,
        "mineru_official_model": _settings.mineru_official_model,
        "mineru_api_key_set": bool(_settings.mineru_api_key),
        "document_extract_concurrency": _settings.document_extract_concurrency,
        "document_chunk_max_tokens": _settings.document_chunk_max_tokens,
        "document_chunk_mode": _settings.document_chunk_mode,
        "search_strategy": _settings.search_strategy,
        "search_top_k": _settings.search_top_k,
        "sag_language": _settings.sag_language,
        "sources": dict(_MODEL_CONFIG_SOURCES),
        "locked_fields": sorted(_LOCKABLE_LLM_FIELDS) if _settings.lock_llm_config else [],
    }


def _conversation_relative(value: object, default: str) -> str:
    raw = str(value or default).strip().replace("\\", "/")
    root = Path(_settings.conversation_export_root).resolve()
    host_root = (
        Path(_settings.conversation_host_root).expanduser().resolve()
        if _settings.conversation_host_root
        else None
    )
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        resolved_candidate = candidate.resolve()
        try:
            candidate = resolved_candidate.relative_to(root)
        except ValueError as error:
            if host_root is None:
                raise ConfigurationError("当前部署未配置会话归档宿主机根目录") from error
            try:
                candidate = resolved_candidate.relative_to(host_root)
            except ValueError as error:
                raise ConfigurationError("会话目录必须位于已挂载的知识库根目录内") from error
    resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ConfigurationError("会话目录不能越过受控挂载根目录") from error
    if not str(relative) or str(relative) == ".":
        raise ConfigurationError("会话目录不能直接使用挂载根目录")
    return relative.as_posix()


def _conversation_display(relative: str) -> str:
    root = (
        Path(_settings.conversation_host_root).expanduser()
        if _settings.conversation_host_root
        else Path(_settings.conversation_export_root)
    )
    return str(root / relative)


def effective_system_preferences() -> dict[str, object]:
    display_root = (
        str(Path(_settings.conversation_host_root).expanduser())
        if _settings.conversation_host_root
        else str(Path(_settings.folder_sync_root).expanduser())
    )
    return {
        "timezone": _settings.timezone,
        "root_dir": display_root,
        "conversation_archive_dir": _conversation_display(_settings.conversation_archive_dir),
        "conversation_ingest_dir": _conversation_display(_settings.conversation_ingest_dir),
        "processing_log_retention_days": _processing_log_retention_days,
    }


def _apply_dsh_integration_overrides(values: dict) -> None:
    """Apply the public DSH integration settings without ever logging its token."""

    if isinstance(values.get("enabled"), bool):
        _settings.dsh_enabled = values["enabled"]
    if isinstance(values.get("gateway_base"), str) and values["gateway_base"].strip():
        _settings.dsh_gateway_base = values["gateway_base"].strip().rstrip("/")
    if isinstance(values.get("gateway_token"), str) and values["gateway_token"].strip():
        _settings.dsh_gateway_token = values["gateway_token"].strip()
    if isinstance(values.get("gateway_timeout_seconds"), (int, float)):
        _settings.dsh_gateway_timeout_seconds = float(values["gateway_timeout_seconds"])


def effective_dsh_integration_config() -> dict[str, object]:
    return {
        "enabled": _settings.dsh_enabled,
        "gateway_base": _settings.dsh_gateway_base or "",
        "gateway_token_set": bool(_settings.dsh_gateway_token),
        "gateway_timeout_seconds": _settings.dsh_gateway_timeout_seconds,
    }


async def save_dsh_integration_config(session: AsyncSession, patch: dict) -> dict[str, object]:
    """Persist one local installation's DSH gateway connection configuration."""

    row = await _load_row(session, _DSH_INTEGRATION_KEY)
    stored = dict(row.value) if row and isinstance(row.value, dict) else {}
    if "enabled" in patch:
        stored["enabled"] = bool(patch["enabled"])
    if isinstance(patch.get("gateway_base"), str) and patch["gateway_base"].strip():
        stored["gateway_base"] = patch["gateway_base"].strip().rstrip("/")
    if isinstance(patch.get("gateway_token"), str) and patch["gateway_token"].strip():
        # Write-only secret: callers receive only gateway_token_set.
        stored["gateway_token"] = patch["gateway_token"].strip()
    if isinstance(patch.get("gateway_timeout_seconds"), (int, float)):
        stored["gateway_timeout_seconds"] = float(patch["gateway_timeout_seconds"])
    enabled = stored.get("enabled", _settings.dsh_enabled)
    gateway_base = str(stored.get("gateway_base") or _settings.dsh_gateway_base or "").strip()
    gateway_token = str(stored.get("gateway_token") or _settings.dsh_gateway_token or "").strip()
    if enabled and (not gateway_base or not gateway_token):
        raise ConfigurationError("启用 DSH 会话前，请先配置 DSH 网关地址和服务令牌")
    if row is None:
        session.add(Setting(scope=_SCOPE, key=_DSH_INTEGRATION_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()
    _apply_dsh_integration_overrides(stored)
    return effective_dsh_integration_config()


async def save_system_preferences(session: AsyncSession, patch: dict) -> dict[str, object]:
    global _processing_log_retention_days
    row = await _load_row(session, _PREFERENCES_KEY)
    stored = dict(row.value) if row and isinstance(row.value, dict) else {}
    timezone = patch.get("timezone")
    if isinstance(timezone, str):
        stored["timezone"] = timezone
    if "processing_log_retention_days" in patch and patch["processing_log_retention_days"] is not None:
        stored["processing_log_retention_days"] = int(patch["processing_log_retention_days"])

    root_dir = patch.get("root_dir")
    if isinstance(root_dir, str):
        candidate = Path(root_dir.strip()).expanduser()
        if not candidate.is_absolute():
            raise ConfigurationError("根目录必须使用绝对路径")
        resolved_root = candidate.resolve()
        stored["root_dir"] = str(resolved_root)
        configured_host_root = str(_settings.conversation_host_root or "").strip()
        sync_root = Path(_settings.folder_sync_root).expanduser().resolve()
        if (
            configured_host_root
            and str(resolved_root) == str(sync_root)
            and str(Path(configured_host_root).expanduser().resolve()) != str(sync_root)
        ):
            raise ConfigurationError(
                "当前 Docker 已将宿主机目录映射到 /sync；请填写宿主机映射路径，而不是 /sync"
            )
        if resolved_root.exists() and not resolved_root.is_dir():
            raise ConfigurationError("根目录必须是文件夹")
        if resolved_root.is_dir() and os.access(resolved_root, os.R_OK | os.W_OK):
            _settings.folder_sync_root = str(resolved_root)
            _settings.conversation_export_root = str(resolved_root)
            _settings.conversation_host_root = str(resolved_root)
        else:
            if (
                not configured_host_root
                or str(resolved_root) != str(Path(configured_host_root).expanduser().resolve())
                or not sync_root.is_dir()
            ):
                raise ConfigurationError(
                    "Docker 模式只能保存已配置的宿主机映射路径；"
                    "请先修改 SAG_SYNC_HOST_PATH 并重新部署"
                )

    for field, default in (
        ("conversation_archive_dir", _DEFAULT_CONVERSATION_ARCHIVE_DIR),
        ("conversation_ingest_dir", _DEFAULT_CONVERSATION_INGEST_DIR),
    ):
        if field in patch and patch[field] is not None:
            stored[field] = _conversation_relative(patch[field], default)

    if row is None:
        session.add(Setting(scope=_SCOPE, key=_PREFERENCES_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()

    if isinstance(stored.get("timezone"), str):
        _settings.timezone = stored["timezone"]
    _processing_log_retention_days = int(
        stored.get("processing_log_retention_days")
        or _DEFAULT_PROCESSING_LOG_RETENTION_DAYS
    )
    archive_relative = _conversation_relative(stored.get("conversation_archive_dir"), _DEFAULT_CONVERSATION_ARCHIVE_DIR)
    ingest_relative = _conversation_relative(stored.get("conversation_ingest_dir"), _DEFAULT_CONVERSATION_INGEST_DIR)
    _settings.conversation_archive_dir = archive_relative
    _settings.conversation_ingest_dir = ingest_relative
    return {
        "timezone": _settings.timezone,
        "root_dir": (
            str(Path(_settings.conversation_host_root).expanduser())
            if _settings.conversation_host_root
            else str(Path(_settings.folder_sync_root).expanduser())
        ),
        "conversation_archive_dir": _conversation_display(archive_relative),
        "conversation_ingest_dir": _conversation_display(ingest_relative),
        "processing_log_retention_days": int(
            stored.get("processing_log_retention_days")
            or _DEFAULT_PROCESSING_LOG_RETENTION_DAYS
        ),
    }


async def processing_log_retention_days(session: AsyncSession) -> int:
    return _processing_log_retention_days


async def save_model_config(session: AsyncSession, patch: dict) -> dict:
    """合并保存模型配置：入库 + 覆盖 settings 单例；返回生效配置（脱敏）。

    约定（配合 `exclude_unset`）：
    - 字段未出现 → 保持不变；
    - 密钥字段值为空 → 忽略（保留原密钥，避免误清空）；空值仅经显式非空覆盖；
    - 可空字段（base_url / dimensions）值为空 → 置 None（清除）。
    """
    await _reject_incompatible_embedding_change(session, patch)
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    stored = _normalize_overrides(raw)

    for key, value in patch.items():
        if key not in _FIELDS:
            continue
        if _settings.lock_llm_config and key in _LOCKABLE_LLM_FIELDS:
            continue
        if key in _SECRET_FIELDS:
            if value:  # 仅非空才更新；空/None 保留原值
                stored[key] = str(value)
            continue
        if key in _NULLABLE_FIELDS and (value is None or value == ""):
            stored[key] = None
            continue
        stored[key] = value

    stored = _normalize_overrides(stored)

    if row is None:
        session.add(Setting(scope=_SCOPE, key=_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()

    apply_overrides(_settings, stored)
    return effective_model_config()


async def save_302_quick_setup(session: AsyncSession, api_key: str) -> dict:
    """用单个 302.AI Key 写入生成、向量、MinerU 与快速检索预设。"""
    return await save_model_config(
        session,
        {
            **QUICK_SETUP_302,
            "llm_api_key": api_key,
            "embedding_api_key": api_key,
            "mineru_api_key": api_key,
        },
    )


async def save_302_mineru_setup(session: AsyncSession) -> dict:
    """为已有 302 模型配置复用现有 Key，不把密钥回传给浏览器。"""
    candidates = (
        (_settings.llm_base_url, _settings.llm_api_key),
        (_settings.effective_embedding_base_url, _settings.effective_embedding_api_key),
    )
    for base_url, api_key in candidates:
        parsed = urlparse(base_url or "")
        host = (parsed.hostname or "").lower()
        if host not in {"api.302.ai", "api.302ai.cn"} or not api_key:
            continue
        return await save_model_config(
            session,
            {
                "document_parser": "auto",
                "mineru_provider": "302",
                "mineru_base_url": "https://api.302ai.cn",
                "mineru_api_key": api_key,
                "mineru_version": "2.5",
            },
        )
    raise ConfigurationError("未找到可复用的 302.AI 模型 API Key")
