"""Immutable icon originals and resumable local uploads; no stored public domains."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (CheckConstraint("status IN ('staging', 'ready')", name="status"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    uploader_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(String(32), default="icon")
    filename: Mapped[str] = mapped_column(String(160))
    mime: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_id: Mapped[str] = mapped_column(String(32), default="local")
    object_key: Mapped[str] = mapped_column(String(256), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="staging")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    title: Mapped[str] = mapped_column(String(160), default="", server_default="")
    alt_text: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    caption: Mapped[str] = mapped_column(Text, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    edit_recipe: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
