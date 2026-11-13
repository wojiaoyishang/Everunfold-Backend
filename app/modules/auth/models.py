"""Durable accounts, revocable opaque sessions and bounded authentication attempts."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
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
    # Provenance only; authorization always uses the user's own role/grants.
    permission_preset: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    email: Mapped[str] = mapped_column(String(254), default="", server_default="")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment_reply_email: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    avatar_url: Mapped[str] = mapped_column(Text, default="", server_default="")
    avatar_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_avatars.id", use_alter=True, name="fk_users_avatar_id_account_avatars")
    )
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


Index(
    "uq_users_verified_email",
    func.lower(func.btrim(User.email)),
    unique=True,
    postgresql_where=User.email_verified_at.is_not(None),
)

Index(
    "uq_users_email_normalized",
    func.lower(func.btrim(User.email)),
    unique=True,
    postgresql_where=func.btrim(User.email) != "",
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


class TwoFactorCredential(Base):
    __tablename__ = "two_factor_credentials"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    setup_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_counter: Mapped[int] = mapped_column(Integer, default=-1, server_default="-1")
    recovery_hashes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class LoginChallenge(Base):
    __tablename__ = "login_challenges"

    session_hash: Mapped[str] = mapped_column(
        ForeignKey("browser_sessions.token_hash", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    auth_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


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
