from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SCHEDULE_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class FolderSyncConfigUpdate(BaseModel):
    enabled: bool = False
    directory: str = Field(default="", max_length=512)
    directories: list[str] = Field(default_factory=list, max_length=50)
    mode: str = Field(default="watch", pattern="^(watch|interval|daily|weekly)$")
    interval_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    schedule_time: str = Field(default="00:00", max_length=5)
    weekday: int = Field(default=0, ge=0, le=6)
    include_globs: list[str] = Field(default_factory=lambda: ["**/*.md", "*.md"], max_length=20)
    exclude_globs: list[str] = Field(default_factory=list, max_length=20)
    sync_deletions: bool = False
    image_enrichment_enabled: bool = False
    image_enrichment_mode: str = Field(default="vlm", pattern="^(vlm|ocr)$")
    image_max_count: int = Field(default=20, ge=1, le=50)
    image_max_mb: int = Field(default=10, ge=1, le=50)

    @field_validator("directory")
    @classmethod
    def normalize_directory(cls, value: str) -> str:
        return value.strip().replace("\\", "/").strip("/")

    @field_validator("directories")
    @classmethod
    def normalize_directories(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip().replace("\\", "/").strip("/")
            if item and item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("include_globs", "exclude_globs")
    @classmethod
    def clean_globs(cls, values: list[str]) -> list[str]:
        return [value.strip().replace("\\", "/") for value in values if value.strip()]

    @field_validator("schedule_time")
    @classmethod
    def validate_schedule_time(cls, value: str) -> str:
        normalized = value.strip()
        if not _SCHEDULE_TIME.fullmatch(normalized):
            raise ValueError("定时同步时间必须是 HH:MM 格式")
        return normalized


class FolderSyncConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    enabled: bool
    directory: str
    directories: list[str]
    mode: str
    interval_seconds: int
    schedule_time: str
    weekday: int
    include_globs: list[str]
    exclude_globs: list[str]
    sync_deletions: bool
    image_enrichment_enabled: bool
    image_enrichment_mode: str
    image_max_count: int
    image_max_mb: int
    current_stage: str | None
    current_file: str | None
    last_started_at: datetime | None
    last_finished_at: datetime | None
    last_error: str | None
    last_summary: dict
    today_new: int = 0
    last_7_days_new: int = 0
    mount_root: str
    mount_available: bool
    timezone: str
    image_enrichment_available: bool


class FolderSyncRunOut(BaseModel):
    job_id: str
    status: str


class FolderSyncDirectory(BaseModel):
    name: str
    path: str


class FolderSyncDirectoryListOut(BaseModel):
    current_path: str
    parent_path: str | None
    directories: list[FolderSyncDirectory]
    mount_root: str
    mount_available: bool
