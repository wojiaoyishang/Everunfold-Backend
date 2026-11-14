"""Settings and encrypted short-lived requests private to the writing plugin."""

from datetime import datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

from .schemas import SceneFields


class StoredProvider(TypedDict):
    id: str
    name: str
    base_url: str
    model: str
    key_ciphertext: str | None


class AiWritingSettings(Base):
    __tablename__ = "ai_writing_settings"
    __table_args__ = (CheckConstraint("id = 1 AND revision >= 0", name="singleton_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    fields: Mapped[SceneFields] = mapped_column(JSONB)
    default_provider_id: Mapped[UUID | None] = mapped_column()
    providers: Mapped[list[StoredProvider]] = mapped_column(JSONB)


class AiWritingRequest(Base):
    __tablename__ = "ai_writing_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'unknown')", name="status"
        ),
        Index("ix_ai_writing_requests_status_created_at", "status", "created_at"),
        Index("ix_ai_writing_requests_actor_created_at", "actor_id", "created_at"),
        Index("ix_ai_writing_requests_finished_at", "finished_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[UUID | None] = mapped_column()
    scene: Mapped[str | None] = mapped_column(String(32))
    field: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[UUID | None] = mapped_column()
    input_ciphertext: Mapped[str | None] = mapped_column(Text)
    output_ciphertext: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
