"""Durable, user-facing processing events for knowledge sources."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin


class ProcessingLog(IDMixin, TimestampMixin, Base):
    __tablename__ = "processing_logs"
    __table_args__ = (
        Index("ix_processing_logs_source_created", "source_id", "created_at"),
        Index("ix_processing_logs_document_created", "document_id", "created_at"),
    )

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    stage: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
