from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.core.config import settings
from sag_api.db.base import Base, IDMixin, TimestampMixin


PROJECT_SCHEMA = "project_mgmt" if settings.database_url.startswith(("postgresql", "postgres")) else None
PROJECT_TABLE_PREFIX = "" if PROJECT_SCHEMA else "pm_"
USER_FK = "public.users.id" if PROJECT_SCHEMA else "users.id"
BOOL_DEFAULT = "false" if PROJECT_SCHEMA else "0"


def _table_args() -> dict[str, str]:
    return {"schema": PROJECT_SCHEMA} if PROJECT_SCHEMA else {}


def _fk(table: str, column: str = "id") -> str:
    qualified = f"{PROJECT_SCHEMA}.{table}" if PROJECT_SCHEMA else f"{PROJECT_TABLE_PREFIX}{table}"
    return f"{qualified}.{column}"


class Project(IDMixin, TimestampMixin, Base):
    __tablename__ = f"{PROJECT_TABLE_PREFIX}projects"
    __table_args__ = _table_args()

    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(32), default="#3b82f6")
    project_type: Mapped[str] = mapped_column(String(16), default="stage")
    stage_index: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=BOOL_DEFAULT)
    legacy_source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class Task(IDMixin, TimestampMixin, Base):
    __tablename__ = f"{PROJECT_TABLE_PREFIX}tasks"
    __table_args__ = _table_args()

    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey(_fk("projects"), ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey(_fk("tasks"), ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="todo", index=True)
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_type: Mapped[str] = mapped_column(String(16), default="task")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    remind_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    complete_time: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    repeat_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reminders: Mapped[list] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    legacy_source_file: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    requirement_id: Mapped[str | None] = mapped_column(ForeignKey(_fk("requirements"), ondelete="SET NULL"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class TaskDailyNode(Base):
    __tablename__ = f"{PROJECT_TABLE_PREFIX}task_daily_nodes"
    __table_args__ = _table_args()

    task_id: Mapped[str] = mapped_column(ForeignKey(_fk("tasks"), ondelete="CASCADE"), primary_key=True)
    node_date: Mapped[date] = mapped_column(Date, primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="todo")
    note: Mapped[str] = mapped_column(Text, default="")


class Requirement(IDMixin, TimestampMixin, Base):
    """A product idea before it is scheduled into project work."""

    __tablename__ = f"{PROJECT_TABLE_PREFIX}requirements"
    __table_args__ = _table_args()

    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey(_fk("projects"), ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(120), default="自己提出")
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    priority: Mapped[str] = mapped_column(String(32), default="中")
    value: Mapped[str] = mapped_column(String(32), default="中")
    urgency: Mapped[str] = mapped_column(String(32), default="中")
    evaluation: Mapped[str] = mapped_column(Text, default="")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    converted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class WorkReport(IDMixin, TimestampMixin, Base):
    """Immutable-on-confirmation daily or weekly report with task snapshot."""

    __tablename__ = f"{PROJECT_TABLE_PREFIX}work_reports"
    __table_args__ = _table_args()

    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), index=True)
    report_type: Mapped[str] = mapped_column(String(12), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[str] = mapped_column(Text, default="")
    next_plan: Mapped[str] = mapped_column(Text, default="")
    reflection: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class ProjectStage(IDMixin, TimestampMixin, Base):
    __tablename__ = f"{PROJECT_TABLE_PREFIX}stage_definitions"
    __table_args__ = _table_args()

    user_id: Mapped[str] = mapped_column(ForeignKey(USER_FK, ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(32), default="#888780")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    has_input: Mapped[bool] = mapped_column(Boolean, default=False, server_default=BOOL_DEFAULT)
