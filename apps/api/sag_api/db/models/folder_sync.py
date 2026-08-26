"""Persistent configuration and manifest for managed local-folder synchronization."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class FolderSyncConfig(TimestampMixin, Base):
    __tablename__ = "folder_sync_configs"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Always a normalized relative path below Settings.folder_sync_root.
    directory: Mapped[str] = mapped_column(String(512), default="")
    # Canonical multi-folder configuration. ``directory`` remains for legacy clients.
    directories: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(16), default="watch")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    # Daily and weekly schedules are interpreted in Settings.timezone.
    schedule_time: Mapped[str] = mapped_column(String(5), default="00:00")
    # Monday is 0, matching datetime.weekday().
    weekday: Mapped[int] = mapped_column(Integer, default=0)
    include_globs: Mapped[list] = mapped_column(JSON, default=lambda: ["**/*.md", "*.md"])
    exclude_globs: Mapped[list] = mapped_column(JSON, default=list)
    sync_deletions: Mapped[bool] = mapped_column(Boolean, default=False)
    image_enrichment_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    image_enrichment_mode: Mapped[str] = mapped_column(String(8), default="vlm")
    image_max_count: Mapped[int] = mapped_column(Integer, default=20)
    image_max_mb: Mapped[int] = mapped_column(Integer, default=10)
    # Persisted while an image-enriched file is being prepared for the UI.
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_summary: Mapped[dict] = mapped_column(JSON, default=dict)


class FolderSyncEntry(IDMixin, TimestampMixin, Base):
    __tablename__ = "folder_sync_entries"
    __table_args__ = (
        UniqueConstraint("source_id", "relative_path", name="uq_folder_sync_entry_source_path"),
        Index("ix_folder_sync_entries_source", "source_id"),
    )

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(String(1024))
    # Raw file fingerprint is independent from optional image-enrichment settings.
    raw_content_hash: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    image_enrichment_status: Mapped[str] = mapped_column(String(16), default="not_requested")
    image_enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
