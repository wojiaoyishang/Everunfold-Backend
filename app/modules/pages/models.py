"""Private page drafts and public source snapshots; registrations arrive only at deployment."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ManagedPage(Base):
    __tablename__ = "managed_pages"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="positive_revision"),
        CheckConstraint("internal_revision >= 0", name="nonnegative_internal_revision"),
        CheckConstraint("source IN ('custom', 'registered')", name="valid_source"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    implementation_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    internal_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    internal_revision: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FrontendPageRegistration(Base):
    __tablename__ = "frontend_page_registrations"
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    page_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
