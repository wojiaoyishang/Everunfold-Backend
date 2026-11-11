"""Settings audit and durable delivery intent share the business transaction."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NodePolicy(Base):
    __tablename__ = "frontend_node_policies"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision"),)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SettingsAudit(Base):
    __tablename__ = "settings_audit"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    resource: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SettingsDeliveryEvent(Base):
    __tablename__ = "settings_delivery_events"
    __table_args__ = (
        UniqueConstraint("node_id", "source_epoch", "resource", "revision"),
        CheckConstraint("revision >= 1 AND attempts >= 0", name="version_attempts"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id: Mapped[str] = mapped_column(String(64))
    source_epoch: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    resource: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(80))


class FrontendExtensionInventory(Base):
    __tablename__ = "frontend_extension_inventories"
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MailTestDelivery(Base):
    """Private durable test requests; credentials are never copied into jobs or broker payloads."""

    __tablename__ = "mail_test_deliveries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    recipient: Mapped[str] = mapped_column(String(254))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TranslationRevision(Base):
    __tablename__ = "translation_revisions"
    __table_args__ = (CheckConstraint("id = 1 AND revision >= 1", name="singleton_revision"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class TranslationOverride(Base):
    __tablename__ = "translation_overrides"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "locale",
            "message_key",
            name="uq_translation_overrides_scope_locale_message_key",
        ),
        CheckConstraint("revision >= 1", name="positive_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    owner: Mapped[str] = mapped_column(String(128))
    message_key: Mapped[str] = mapped_column(String(512))
    locale: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(4000))
    source_hash: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(4000))
    revision: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
