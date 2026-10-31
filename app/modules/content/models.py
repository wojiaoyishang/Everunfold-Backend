"""Initial public-content storage. No WordPress authentication data is retained."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import UniqueConstraint as Unique
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Content(Base):
    __tablename__ = "contents"
    __table_args__ = (
        Unique("source", "source_id"),
        CheckConstraint("kind IN ('article', 'page')", name="kind"),
        CheckConstraint("publication_status IN ('published', 'draft', 'withdrawn')", name="status"),
        CheckConstraint(
            "visibility IN ('public', 'unlisted', 'authenticated', 'password')", name="visibility"
        ),
        CheckConstraint("public_generation >= 1", name="generation"),
        CheckConstraint("word_count >= 0 AND reading_minutes >= 1", name="reading_metrics"),
        CheckConstraint("left(path, 1) = '/'", name="absolute_path"),
        CheckConstraint("jsonb_typeof(content) = 'array'", name="content_blocks"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), default="wordpress")
    source_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, unique=True)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    author: Mapped[str] = mapped_column(String(200))
    cover_url: Mapped[str | None] = mapped_column(Text)
    reading_minutes: Mapped[int] = mapped_column(Integer, default=1)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    publication_status: Mapped[str] = mapped_column(String(16), default="published", index=True)
    visibility: Mapped[str] = mapped_column(String(16), default="public", index=True)
    public_generation: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    media_urls: Mapped[dict[str, dict[str, str]]] = mapped_column(JSONB, default=dict)
    taxonomies: Mapped[list[Taxonomy]] = relationship(
        secondary="content_taxonomies", lazy="raise", back_populates="contents"
    )


class Taxonomy(Base):
    __tablename__ = "taxonomies"
    __table_args__ = (
        Unique("kind", "slug"),
        Unique("kind", "source_id", name="uq_taxonomies_kind_source_id"),
        CheckConstraint("kind IN ('category', 'tag')", name="kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    contents: Mapped[list[Content]] = relationship(
        secondary="content_taxonomies", lazy="raise", back_populates="taxonomies"
    )


class ContentTaxonomy(Base):
    __tablename__ = "content_taxonomies"

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True
    )
    taxonomy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomies.id", ondelete="CASCADE"), primary_key=True
    )


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        Unique("source_id"),
        CheckConstraint("status IN ('approved', 'pending', 'hidden')", name="status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[int | None] = mapped_column(Integer)
    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("comments.id"))
    author: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="approved")


class SearchDocument(Base):
    __tablename__ = "search_documents"

    content_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True
    )
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)


class CommentLike(Base):
    """Anonymous browser preferences; never an account or an access credential."""

    __tablename__ = "comment_likes"

    comment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), primary_key=True
    )
    visitor_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SiteSettings(Base):
    __tablename__ = "site_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("generation >= 1", name="generation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_epoch: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=uuid.uuid4)
    generation: Mapped[int] = mapped_column(Integer, default=1)
