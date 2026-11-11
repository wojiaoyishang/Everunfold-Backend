"""Trusted platform adapter boundary for notification delivery."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID


@dataclass(frozen=True)
class NotificationDeliveryRequest:
    delivery_id: UUID
    event_type: str
    event_data: dict[str, Any]
    recipient_id: UUID
    recipient_name: str
    locale: str
    subject: str
    text: str
    href: str


@dataclass(frozen=True)
class NotificationDeliveryResult:
    status: Literal["sent", "failed", "unknown"]
    error: str | None = None


@dataclass(frozen=True)
class NotificationPlatform:
    id: str
    title: str
    description: str
    kind: Literal["direct", "bridge"]
    delivery_semantics: Literal["idempotent", "retry-safe", "unknown-no-retry"]
    deliver: Callable[[NotificationDeliveryRequest], Awaitable[NotificationDeliveryResult]]
