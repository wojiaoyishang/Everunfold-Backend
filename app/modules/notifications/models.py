"""Durable notification events, routes, templates, deliveries and in-app inbox."""

from datetime import datetime
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class NotificationRevision(Base):
    __tablename__ = "notification_revisions"
    __table_args__ = (CheckConstraint("id = 1 AND revision >= 1", name="singleton_revision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class NotificationRoute(Base):
    __tablename__ = "notification_routes"
    __table_args__ = (
        CheckConstraint(
            "recipient_type IN ('user', 'role', 'permission')",
            name="recipient_type",
        ),
        UniqueConstraint(
            "event_type",
            "recipient_type",
            "recipient_value",
            "platform_id",
            name="uq_notification_route_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(120))
    recipient_type: Mapped[str] = mapped_column(String(16))
    recipient_value: Mapped[str] = mapped_column(String(160))
    platform_id: Mapped[str] = mapped_column(String(120))
    mail_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    template_key: Mapped[str] = mapped_column(String(120), default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "platform_id",
            "locale",
            name="uq_notification_template_variant",
        ),
        CheckConstraint("revision >= 1", name="positive_revision"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(120))
    platform_id: Mapped[str] = mapped_column(String(120))
    locale: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        CheckConstraint("event_version >= 1", name="positive_event_version"),
        CheckConstraint("jsonb_typeof(data) = 'object'", name="object_data"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    event_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    resource_type: Mapped[str] = mapped_column(String(40), default="")
    resource_id: Mapped[str] = mapped_column(String(120), default="")
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", "platform_id", name="uq_notification_delivery"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'failed', 'unknown', 'suppressed')",
            name="delivery_status",
        ),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("notification_events.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    platform_id: Mapped[str] = mapped_column(String(120))
    mail_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mail_config_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("notification_templates.id", ondelete="SET NULL")
    )
    locale: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text, default="")
    href: Mapped[str] = mapped_column(Text, default="")
    destination: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationInbox(Base):
    __tablename__ = "notification_inbox"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_notification_inbox"),
        Index("ix_notification_inbox_user_created", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("notification_events.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    href: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
