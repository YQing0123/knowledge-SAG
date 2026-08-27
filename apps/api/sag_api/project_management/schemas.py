from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StageInput(BaseModel):
    id: str | None = None
    label: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#888780", max_length=32)
    sort_order: int = Field(default=0, ge=0)
    has_input: bool = False


class StageOut(StageInput):
    model_config = ConfigDict(from_attributes=True)
    id: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    color: str = "#3b82f6"
    project_type: str = Field(default="stage", pattern="^(stage|nostage)$")
    stage_index: int = Field(default=0, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    sort_order: int = Field(default=0, ge=0)
    legacy_source_path: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectCreate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    color: str | None = Field(default=None, max_length=32)
    project_type: str | None = Field(default=None, pattern="^(stage|nostage)$")
    stage_index: int | None = Field(default=None, ge=0)
    start_date: date | None = None
    end_date: date | None = None
    sort_order: int | None = Field(default=None, ge=0)
    archived: bool | None = None
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dates(self) -> "ProjectPatch":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class ProjectOut(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    archived: bool
    version: int
    created_at: datetime
    updated_at: datetime
    task_count: int = 0
    active_count: int = 0


class TaskCreate(BaseModel):
    project_id: str
    parent_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    status: str = Field(default="todo", pattern="^(todo|in-progress|blocked|done|cancelled)$")
    priority: str | None = None
    task_type: str = Field(default="task", pattern="^(task|recurring)$")
    start_date: date | None = None
    due_date: date | None = None
    remind_date: date | None = None
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    repeat_rule: dict[str, Any] | None = None
    reminders: list[str] = Field(default_factory=list)
    sort_order: int = Field(default=0, ge=0)
    legacy_source_file: str | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskCreate":
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date must be on or after start_date")
        return self


class TaskPatch(BaseModel):
    project_id: str | None = None
    parent_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: str | None = Field(default=None, pattern="^(todo|in-progress|blocked|done|cancelled)$")
    priority: str | None = None
    task_type: str | None = Field(default=None, pattern="^(task|recurring)$")
    start_date: date | None = None
    due_date: date | None = None
    remind_date: date | None = None
    notes: str | None = None
    tags: list[str] | None = None
    repeat_rule: dict[str, Any] | None = None
    reminders: list[str] | None = None
    sort_order: int | None = Field(default=None, ge=0)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dates(self) -> "TaskPatch":
        if self.start_date and self.due_date and self.due_date < self.start_date:
            raise ValueError("due_date must be on or after start_date")
        return self


class TaskOut(TaskCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    version: int
    complete_time: datetime | None
    created_at: datetime
    updated_at: datetime
    daily_nodes: list["DailyNodeOut"] = Field(default_factory=list)


class DailyNodeInput(BaseModel):
    state: str = Field(pattern="^(todo|done|skip)$")
    note: str = ""


class DailyNodeOut(DailyNodeInput):
    model_config = ConfigDict(from_attributes=True)
    task_id: str
    node_date: date


class SnapshotOut(BaseModel):
    projects: list[ProjectOut]
    tasks: list[TaskOut]
    stages: list[StageOut]
    generated_at: datetime


class ReorderInput(BaseModel):
    task_ids: list[str] = Field(min_length=1)
