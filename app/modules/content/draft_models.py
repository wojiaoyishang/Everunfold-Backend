"""Working drafts and durable operation/revision history; never public snapshot input."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ContentDraft(Base):
    __tablename__ = "content_drafts"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("jsonb_typeof(body) = 'array'", name="body_blocks"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    type_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    excerpt: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    editor_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_revision: Mapped[int | None] = mapped_column(Integer)
    public_path: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DraftRevision(Base):
    __tablename__ = "content_draft_revisions"

    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DraftReceipt(Base):
    __tablename__ = "content_draft_receipts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    digest: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), index=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
