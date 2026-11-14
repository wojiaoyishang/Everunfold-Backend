"""Private page drafts and public source snapshots; registrations arrive only at deployment."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PageTranslationGroup(Base):
    __tablename__ = "page_translation_groups"
    __table_args__ = (CheckConstraint("revision >= 1", name="positive_revision"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "managed_pages.id",
            name="fk_page_groups_source",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        unique=True,
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)


class PageTemplate(Base):
    __tablename__ = "page_templates"
    __table_args__ = (CheckConstraint("revision >= 1", name="positive_revision"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500))
    revision: Mapped[int] = mapped_column(Integer)
    code: Mapped[dict[str, Any]] = mapped_column(JSONB)
    applied_revision: Mapped[int] = mapped_column(Integer)
    draft_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))


class ManagedPage(Base):
    __tablename__ = "managed_pages"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="positive_revision"),
        CheckConstraint("internal_revision >= 0", name="nonnegative_internal_revision"),
        CheckConstraint("source IN ('custom', 'registered')", name="valid_source"),
        CheckConstraint(
            "(source = 'custom' AND type_id = 'core.custom') "
            "OR (source = 'registered' AND type_id <> 'core.custom')",
            name="type_source",
        ),
        CheckConstraint("template_id IS NULL OR source = 'custom'", name="template_source"),
        CheckConstraint(
            "(source = 'custom' AND language IS NOT NULL AND translation_group_id IS NOT NULL) "
            "OR (source = 'registered' AND language IS NULL AND translation_group_id IS NULL)",
            name="translation_source",
        ),
        UniqueConstraint("translation_group_id", "language", name="uq_pages_group_language"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    type_id: Mapped[str] = mapped_column(String(100), index=True)
    template_id: Mapped[UUID | None] = mapped_column(ForeignKey("page_templates.id"), index=True)

    @property
    def implementation_id(self) -> str | None:
        return self.type_id if self.source == "registered" else None

    revision: Mapped[int] = mapped_column(Integer, default=1)
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(32))
    translation_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "page_translation_groups.id",
            name="fk_pages_translation_group",
            deferrable=True,
            initially="DEFERRED",
        )
    )
    translated_from_revision: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    internal_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    internal_revision: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    applied_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PageRevisionFields:
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    action: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    from_revision: Mapped[int | None] = mapped_column(Integer)


class ManagedPageRevision(PageRevisionFields, Base):
    __tablename__ = "managed_page_revisions"
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("managed_pages.id", ondelete="CASCADE"), primary_key=True
    )
    owner: Mapped[ManagedPage] = relationship()


class PageTemplateRevision(PageRevisionFields, Base):
    __tablename__ = "page_template_revisions"
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("page_templates.id", ondelete="CASCADE"), primary_key=True
    )
    owner: Mapped[PageTemplate] = relationship()


class FrontendPageRegistration(Base):
    __tablename__ = "frontend_page_registrations"
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    page_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
