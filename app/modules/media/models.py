"""Immutable icon originals and resumable local uploads; no stored public domains."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
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
    sha256: Mapped[str] = mapped_column(String(64), index=True)
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

    icon_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    purge_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaUploadReceipt(Base):
    """Request identity is separate from the content identity reused across uploads."""

    __tablename__ = "media_upload_receipts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    signature: Mapped[str] = mapped_column(String(64))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id"), index=True)
    reused: Mapped[bool] = mapped_column(Boolean)


class MediaRemovalReceipt(Base):
    """Frozen removal scope; retries never capture subsequently uploaded records."""

    __tablename__ = "media_removal_receipts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_assets.id"))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    delete_original: Mapped[bool] = mapped_column(Boolean)
    safe_delete: Mapped[bool] = mapped_column(Boolean)
    token: Mapped[str] = mapped_column(String(64))
    record_ids: Mapped[list[str]] = mapped_column(JSONB)
    file_ids: Mapped[list[str]] = mapped_column(JSONB)
    file_retained: Mapped[bool] = mapped_column(Boolean)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
