from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, computed_field, field_validator

from sag_api.enums import DocumentStatus

from sag_api.schemas.job import JobOut


class MessageItem(BaseModel):
    text: str
    author: str | None = None
    role: str | None = None
    ts: str | None = None
    thread: str | None = None


class IngestRequest(BaseModel):
    """统一写入：文本或一批消息，二选一。"""

    text: str | None = None
    title: str | None = None
    messages: list[MessageItem] | None = Field(default=None)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: DocumentStatus
    chunk_count: int
    event_count: int
    progress: int
    token_usage: int
    error: str | None
    error_layer: str | None = None
    error_stage: str | None = None
    parser_provider: str | None = None
    mineru_provider: str | None = None
    mineru_model: str | None = None
    parser_status: str | None = None
    fallback_from: str | None = None
    fallback_reason: str | None = None
    octx_installation_id: str | None = Field(default=None, exclude=True)
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def original_file_available(self) -> bool:
        """OCTX packages can carry parsed concepts without original attachment bytes."""
        return self.octx_installation_id is None

    @field_validator("error", mode="before")
    @classmethod
    def redact_storage_error(cls, value: object, info: ValidationInfo) -> object:
        """Keep SQL and local storage details in server logs, not in the UI."""
        if not isinstance(value, str):
            return value
        normalized = value.lower()
        sql_markers = (
            "sqlite3.",
            "sqlalchemy.exc",
            "[sql:",
            "[parameters:",
            "sqlalche.me/e/",
        )
        if not any(marker in normalized for marker in sql_markers):
            return value
        if info.data.get("status") == DocumentStatus.DELETE_FAILED:
            return "文档删除失败，请重试；若仍失败，请查看服务日志。"
        if "foreign key constraint failed" in normalized:
            return "信息源初始化未完成，文档尚未入库，请重试。"
        return "文档入库失败，请重试；若仍失败，请查看服务日志。"


class ReprocessFailedDocumentsOut(BaseModel):
    """信源级失败文档重试结果。

    只把调用时仍处于 FAILED 的文档加入队列；READY、处理中和暂停中的
    文档不会被这次操作触碰，避免一键恢复造成重复抽取。
    """

    requested: int
    queued: int
    skipped: int
    jobs: list[JobOut]


class ReprocessCurrentModelDocumentsOut(BaseModel):
    """使用当前 LLM 配置重新处理一个信源中尚未就绪的文档。"""

    requested: int
    queued: int
    stopping: int
    skipped: int
    model: str
    jobs: list[JobOut]
