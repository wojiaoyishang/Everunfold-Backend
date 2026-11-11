"""Versioned notification administration and private inbox contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import PublicModel, PublicResponse

RecipientType = Literal["user", "role", "permission"]
DeliveryStatus = Literal["pending", "sending", "sent", "failed", "unknown", "suppressed"]


class NotificationEventView(PublicModel):
    id: str
    title: str
    description: str
    variables: list[str]


class NotificationPlatformView(PublicModel):
    id: str
    title: str
    description: str
    kind: Literal["direct", "bridge"]
    builtin: bool
    available: bool
    delivery_semantics: Literal["idempotent", "retry-safe", "unknown-no-retry"]


class NotificationRouteView(PublicModel):
    id: UUID
    event_type: str
    recipient_type: RecipientType
    recipient_value: str
    platform_id: str
    template_key: str = "default"
    enabled: bool


class NotificationRouteInput(PublicModel):
    event_type: str = Field(min_length=1, max_length=120)
    recipient_type: RecipientType
    recipient_value: str = Field(min_length=1, max_length=160)
    platform_id: str = Field(min_length=1, max_length=120)
    template_key: str = Field(default="default", pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    enabled: bool = True


class NotificationTemplateView(PublicModel):
    event_type: str
    platform_id: str
    locale: str
    subject: str
    text: str
    href: str = ""
    enabled: bool
    custom: bool
    revision: int = Field(ge=0)


class SaveNotificationTemplate(PublicModel):
    expected_revision: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=120)
    platform_id: str = Field(min_length=1, max_length=120)
    locale: str = Field(pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
    subject: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=10000)
    enabled: bool = True
    reset: bool = False


class SaveNotificationRoutes(PublicModel):
    expected_revision: int = Field(ge=1)
    routes: list[NotificationRouteInput] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_routes(self) -> "SaveNotificationRoutes":
        keys = [
            (item.event_type, item.recipient_type, item.recipient_value, item.platform_id)
            for item in self.routes
        ]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate notification routes")
        return self


class NotificationDeliveryView(PublicModel):
    id: UUID
    event_type: str
    user_id: UUID
    user_name: str
    platform_id: str
    locale: str
    status: DeliveryStatus
    attempts: int
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class NotificationState(PublicModel):
    revision: int = Field(ge=1)
    events: list[NotificationEventView]
    platforms: list[NotificationPlatformView]
    routes: list[NotificationRouteView]
    templates: list[NotificationTemplateView]
    deliveries: list[NotificationDeliveryView]
    locales: list[str]


class NotificationInboxItem(PublicModel):
    id: UUID
    title: str
    text: str
    href: str
    created_at: datetime
    read_at: datetime | None = None


class NotificationInbox(PublicModel):
    unread: int = Field(ge=0)
    items: list[NotificationInboxItem]


class MarkNotificationsRead(PublicModel):
    ids: list[UUID] = Field(default_factory=list, max_length=100)


class NotificationsContract(PublicModel):
    state: PublicResponse[NotificationState]
    routes: SaveNotificationRoutes
    template: SaveNotificationTemplate
    inbox: PublicResponse[NotificationInbox]
    mark_read: MarkNotificationsRead
