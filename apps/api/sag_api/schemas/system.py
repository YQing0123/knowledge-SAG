from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from sag_api.core.model_providers import ModelProviderId
from sag_api.enums import SearchStrategy


class QuickModelSetupRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("API Key 不能为空")
        return value


class SystemPreferencesUpdate(BaseModel):
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    root_dir: str | None = Field(default=None, min_length=1, max_length=600)
    conversation_archive_dir: str | None = Field(default=None, min_length=1, max_length=600)
    conversation_ingest_dir: str | None = Field(default=None, min_length=1, max_length=600)
    processing_log_retention_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("必须使用有效的 IANA 时区，例如 Asia/Shanghai") from error
        return normalized


class DshIntegrationUpdate(BaseModel):
    enabled: bool | None = None
    gateway_base: str | None = Field(default=None, min_length=1, max_length=600)
    gateway_token: str | None = Field(default=None, min_length=1, max_length=1000)
    gateway_timeout_seconds: float | None = Field(default=None, ge=1, le=600)

    @field_validator("gateway_base")
    @classmethod
    def validate_gateway_base(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("DSH 网关地址必须是 http 或 https URL")
        return normalized


class ModelConfigUpdate(BaseModel):
    """模型与知识库配置的部分更新（未出现的字段保持不变）。

    密钥字段留空表示「保持原值」（不清空）；base_url / dimensions 留空表示清除。
    """

    llm_provider: ModelProviderId | None = None
    llm_base_url: str | None = Field(default=None, max_length=500)
    llm_api_key: str | None = Field(default=None, max_length=500)
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    llm_context_window: int | None = Field(default=None, ge=1024, le=2_000_000)
    llm_timeout_ms: int | None = Field(default=None, ge=1_000, le=600_000)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)
    llm_structured_output_mode: Literal[
        "auto", "json_schema", "json_object", "prompt_only"
    ] | None = None

    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_base_url: str | None = Field(default=None, max_length=500)
    embedding_api_key: str | None = Field(default=None, max_length=500)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=8192)

    document_parser: Literal["auto", "markitdown", "mineru"] | None = None
    mineru_provider: Literal["302", "official"] | None = None
    mineru_base_url: str | None = Field(default=None, max_length=500)
    mineru_api_key: str | None = Field(default=None, max_length=500)
    mineru_version: Literal["2.0", "2.5"] | None = None
    mineru_official_model: Literal["pipeline", "vlm"] | None = None
    document_extract_concurrency: int | None = Field(default=None, ge=1, le=50)
    document_chunk_max_tokens: int | None = Field(default=None, ge=100, le=100_000)
    document_chunk_mode: Literal["standard", "heading_strict"] | None = None

    search_strategy: SearchStrategy | None = None
    search_top_k: int | None = Field(default=None, ge=1, le=50)
    sag_language: Literal["zh", "en"] | None = None

    @field_validator(
        "document_parser",
        "mineru_provider",
        "mineru_version",
        "mineru_official_model",
    )
    @classmethod
    def reject_null_parser_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("解析器与 MinerU 服务商/模型配置不能为 null")
        return value

    @field_validator("document_extract_concurrency", "document_chunk_max_tokens")
    @classmethod
    def reject_null_document_numbers(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("知识库解析参数不能为 null")
        return value

    @field_validator("document_chunk_mode")
    @classmethod
    def reject_null_chunk_mode(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("切片模式不能为 null")
        return value

    @field_validator("llm_timeout_ms", "llm_max_retries")
    @classmethod
    def reject_null_llm_resilience_fields(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("模型超时与重试次数不能为 null")
        return value

    @field_validator("llm_structured_output_mode")
    @classmethod
    def reject_null_structured_output_mode(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("结构化输出模式不能为 null")
        return value

    @field_validator("llm_provider")
    @classmethod
    def reject_null_llm_provider(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("模型接入方式不能为 null")
        return value


class ModelConfigTestRequest(ModelConfigUpdate):
    """连接测试请求；target 决定测试哪一类模型。"""

    target: Literal["llm", "embedding", "mineru"] = "llm"


class AnswerModelItem(BaseModel):
    id: str = Field(min_length=1, max_length=300)
    display_name: str | None = Field(default=None, max_length=200)
    context_window: int = Field(default=128_000, ge=1024, le=2_000_000)
    max_output_tokens: int = Field(default=8_192, ge=1, le=1_000_000)


class AnswerModelProfileCreate(BaseModel):
    provider_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    protocol: Literal["openai-compatible", "openai-responses"] = "openai-compatible"
    api_key: str = Field(min_length=1, max_length=500)
    models: list[AnswerModelItem] = Field(default_factory=list, max_length=100)
    is_default: bool = False


class AnswerModelProfileUpdate(BaseModel):
    provider_id: str | None = Field(default=None, min_length=1, max_length=120)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    protocol: Literal["openai-compatible", "openai-responses"] | None = None
    api_key: str | None = Field(default=None, max_length=500)
    models: list[AnswerModelItem] | None = Field(default=None, max_length=100)
    is_default: bool | None = None
    enabled: bool | None = None


class AnswerModelDiscoverRequest(BaseModel):
    """临时探测提供方目录，不会写入数据库。"""

    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1, max_length=500)
    protocol: Literal["openai-compatible", "openai-responses"] = "openai-compatible"


class McpApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
