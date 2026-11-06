"""Versioned, administrator-managed templates; effective grants remain on each user."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserPermissionPreset(Base):
    __tablename__ = "user_permission_presets"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="positive_revision"),
        Index(
            "uq_user_permission_presets_live_name",
            "name_key",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    grants: Mapped[dict[str, dict[str, str]]] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
