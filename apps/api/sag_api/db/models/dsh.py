from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin


class DshSession(IDMixin, TimestampMixin, Base):
    """A SAG-owned durable pointer and configuration record for one DSH conversation."""

    __tablename__ = "dsh_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300), default="新 DSH 会话")
    source_ids: Mapped[list] = mapped_column("source_ids_json", JSON, default=list)
    provider: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(300), default="")
    permission_preset: Mapped[str] = mapped_column(String(100), default="workspace-write")
    mode: Mapped[str] = mapped_column(String(20), default="controlled")
    web_enabled: Mapped[bool] = mapped_column(default=False)
    archived: Mapped[bool] = mapped_column(default=False, index=True)


class DshMessage(IDMixin, TimestampMixin, Base):
    """Replay-safe presentation record for one proxied DSH turn, without DSH secrets."""

    __tablename__ = "dsh_messages"
    __table_args__ = (Index("ix_dsh_messages_session_created_id", "session_id", "created_at", "id"),)

    session_id: Mapped[str] = mapped_column(ForeignKey("dsh_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column("attachments_json", JSON, default=list)
    retrieval_hits: Mapped[int] = mapped_column(default=0)
    context: Mapped[dict] = mapped_column("context_json", JSON, default=dict)
    event_summary: Mapped[list] = mapped_column("event_summary_json", JSON, default=list)
