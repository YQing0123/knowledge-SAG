from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin


class AnswerModelProfile(IDMixin, TimestampMixin, Base):
    """User-managed OpenAI-compatible answer providers and their model catalog."""

    __tablename__ = "answer_model_profiles"
    __table_args__ = (UniqueConstraint("user_id", "provider_id", name="uq_answer_model_profile_user_provider"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(String(200))
    base_url: Mapped[str] = mapped_column(String(500))
    protocol: Mapped[str] = mapped_column(String(32), default="openai-compatible")
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    models: Mapped[list] = mapped_column("models_json", JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
