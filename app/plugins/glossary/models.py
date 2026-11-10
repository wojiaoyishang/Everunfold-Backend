"""Glossary-owned persistence; article snapshots never embed these rows."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    __table_args__ = (
        UniqueConstraint("language", "slug", name="uq_glossary_terms_language_slug"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_glossary_terms_status",
        ),
        CheckConstraint("revision >= 1", name="ck_glossary_terms_revision"),
        CheckConstraint("jsonb_typeof(aliases) = 'array'", name="ck_glossary_terms_aliases"),
        CheckConstraint(
            "match_rules IS NULL OR jsonb_typeof(match_rules) = 'array'",
            name="match_rules",
        ),
        Index("ix_glossary_terms_public", "language", "status", "title"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    language: Mapped[str] = mapped_column(String(32))
    slug: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(120))
    summary: Mapped[str] = mapped_column(Text)
    feature_media_id: Mapped[str] = mapped_column(String(64), default="")
    body_markdown: Mapped[str | None] = mapped_column(Text)
    body: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    auto_match_title: Mapped[bool] = mapped_column(Boolean, default=True)
    match_rules: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GlossaryOperation(Base):
    __tablename__ = "glossary_operations"

    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("glossary_terms.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GlossaryHook(Base):
    __tablename__ = "glossary_hooks"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('all', 'content')",
            name="ck_glossary_hooks_scope",
        ),
        CheckConstraint(
            "mode IN ('selector', 'script')",
            name="ck_glossary_hooks_mode",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_glossary_hooks_status",
        ),
        CheckConstraint("revision >= 1", name="ck_glossary_hooks_revision"),
        CheckConstraint(
            "priority >= 0 AND priority <= 100",
            name="ck_glossary_hooks_priority",
        ),
        CheckConstraint(
            "jsonb_typeof(targets) = 'array'",
            name="ck_glossary_hooks_targets",
        ),
        Index("ix_glossary_hooks_public", "status", "enabled", "scope"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    term_id: Mapped[UUID] = mapped_column(
        ForeignKey("glossary_terms.id", ondelete="RESTRICT"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(String(16), default="selector")
    selector: Mapped[str] = mapped_column(String(256))
    script: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(16))
    targets: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GlossaryHookOperation(Base):
    __tablename__ = "glossary_hook_operations"

    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    hook_id: Mapped[UUID] = mapped_column(
        ForeignKey("glossary_hooks.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
