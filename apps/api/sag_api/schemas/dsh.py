from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DshSessionCreate(BaseModel):
    title: str = Field(default="新 DSH 会话", max_length=300)
    source_ids: list[str] = Field(default_factory=list, max_length=8)
    provider: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=300)
    permission_preset: str = Field(default="workspace-write", max_length=100)
    mode: Literal["controlled", "agent"] = "controlled"
    web_enabled: bool = False


class DshSessionOut(DshSessionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    archived: bool
    created_at: datetime
    updated_at: datetime


class DshSessionPatch(BaseModel):
    """The mutable conversation controls shown in the DSH workspace."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    source_ids: list[str] | None = Field(default=None, max_length=8)
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=300)
    permission_preset: str | None = Field(default=None, max_length=100)
    mode: Literal["controlled", "agent"] | None = None
    web_enabled: bool | None = None


class DshAttachmentInput(BaseModel):
    """A browser-visible SAG attachment handle, never a DSH or filesystem path."""

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=300)
    media_type: str = Field(default="", max_length=80)


class DshStreamRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    query: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] = Field(default_factory=list, max_length=8)
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=300)
    reasoning_effort: str | None = Field(default=None, max_length=100)
    permission_preset: str = Field(default="workspace-write", max_length=100)
    mode: Literal["controlled", "agent"] = "controlled"
    web_enabled: bool = False
    attachments: list[DshAttachmentInput] = Field(default_factory=list, max_length=4)


class DshRetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] = Field(default_factory=list, max_length=8)
    top_k: int = Field(default=8, ge=1, le=20)


class DshMessageCreate(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=100_000)
    attachments: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
    retrieval_hits: int = Field(default=0, ge=0, le=1000)
    context: dict[str, Any] = Field(default_factory=dict)
    event_summary: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class DshMessageOut(DshMessageCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    created_at: datetime
