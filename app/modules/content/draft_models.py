"""Working drafts and durable operation/revision history; never public snapshot input."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TranslationGroup(Base):
    __tablename__ = "content_translation_groups"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "content_drafts.id",
            name="fk_translation_groups_source",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        unique=True,
    )
    type_id: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ContentDraft(Base):
    __tablename__ = "content_drafts"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision"),
        CheckConstraint("jsonb_typeof(body) = 'array'", name="body_blocks"),
        CheckConstraint(
            "(scheduled_for IS NULL AND scheduled_revision IS NULL "
            "AND scheduled_payload IS NULL) OR "
            "(scheduled_for IS NOT NULL AND scheduled_revision IS NOT NULL AND "
            "scheduled_revision >= 1 AND scheduled_payload IS NOT NULL AND "
            "jsonb_typeof(scheduled_payload) = 'object')",
            name="publication_plan",
        ),
        UniqueConstraint(
            "translation_group_id", "language", name="uq_content_drafts_group_language"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    type_id: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    translation_group_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "content_translation_groups.id",
            name="fk_content_drafts_translation_group",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    translated_from_revision: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    excerpt: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    editor_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    access_password_hash: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_revision: Mapped[int | None] = mapped_column(Integer)
    public_path: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_revision: Mapped[int | None] = mapped_column(Integer)
    scheduled_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    schedule_error: Mapped[str | None] = mapped_column(String(40))


class DraftRevision(Base):
    __tablename__ = "content_draft_revisions"

    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(200), default="")
    epoch: Mapped[UUID | None] = mapped_column()
    committed_seq: Mapped[int | None] = mapped_column(Integer)
    parents: Mapped[list[int]] = mapped_column(JSONB, default=list)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DraftBranch(Base):
    __tablename__ = "content_draft_branches"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(String(200))
    base_epoch: Mapped[UUID | None] = mapped_column()
    base_seq: Mapped[int | None] = mapped_column(Integer)
    base_revision: Mapped[int | None] = mapped_column(Integer)
    base: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_revision: Mapped[int | None] = mapped_column(Integer)


class DraftMerge(Base):
    __tablename__ = "content_draft_merges"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("content_draft_branches.id"))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    epoch: Mapped[UUID] = mapped_column()
    target_seq: Mapped[int] = mapped_column(Integer)
    target_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    base: Mapped[dict[str, Any]] = mapped_column(JSONB)
    local: Mapped[dict[str, Any]] = mapped_column(JSONB)
    remote: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    resolved: Mapped[list[str]] = mapped_column(JSONB, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result_revision: Mapped[int | None] = mapped_column(Integer)


class DraftReceipt(Base):
    __tablename__ = "content_draft_receipts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    digest: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), index=True)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
