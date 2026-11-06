"""User keys, application identities and durable callback delivery intent."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ApiApplication(Base):
    __tablename__ = "api_applications"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    audience: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    scopes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    callback_url: Mapped[str] = mapped_column(Text, default="", server_default="")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    auth_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    query_key_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    callback_key_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("api_applications.id"), index=True)
    audience: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    permissions: Mapped[dict[str, str]] = mapped_column(JSONB)
    user_key_epoch: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthCallbackEvent(Base):
    __tablename__ = "auth_callback_events"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("api_applications.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    lease_id: Mapped[UUID | None] = mapped_column()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(80), default="", server_default="")
