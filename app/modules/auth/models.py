"""Durable accounts, revocable opaque sessions and bounded authentication attempts."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="valid_role"),
        CheckConstraint("revision >= 1 AND auth_version >= 1", name="positive_versions"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    auth_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    key_epoch: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    grants: Mapped[dict[str, dict[str, str]]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    email: Mapped[str] = mapped_column(String(254), default="", server_default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="", server_default="")
    bio: Mapped[str] = mapped_column(Text, default="", server_default="")
    website: Mapped[str] = mapped_column(Text, default="", server_default="")
    preferred_locale: Mapped[str] = mapped_column(
        String(32), default="zh-CN", server_default="zh-CN"
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )


class BrowserSession(Base):
    __tablename__ = "browser_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuthAuditEvent(Base):
    __tablename__ = "auth_audit_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    target_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class AccountOperation(Base):
    """Retry receipts contain responses without credentials and salted input fingerprints."""

    __tablename__ = "account_operations"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(40))
    fingerprint: Mapped[str] = mapped_column(String(255))
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
