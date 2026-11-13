"""Transactional publication shared by built-in and plugin event producers."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plugins.loader import plugin_catalog
from app.core.plugins.state import enabled_ids, installed_info
from app.modules.auth.models import User
from app.modules.auth.permissions import user_permissions
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import ACCOUNT_EVENT_IDS, BUILTIN_BY_ID, BUILTIN_EVENTS
from app.modules.notifications.definitions import NotificationEventDefinition
from app.modules.notifications.models import NotificationEvent


@dataclass(frozen=True)
class EventRegistration:
    definition: NotificationEventDefinition
    plugin_id: str | None = None
    build: str | None = None


def event_registry(data: dict[str, Any]) -> dict[str, EventRegistration]:
    registry = {event.id: EventRegistration(event) for event in BUILTIN_EVENTS}
    active = enabled_ids(data)
    builds = {item.id: item.build for item in installed_info(data)}
    for plugin in plugin_catalog().plugins:
        owner = plugin.info
        if owner.id in active and builds.get(owner.id) == owner.build:
            for event in plugin.notification_events:
                registry[event.id] = EventRegistration(event, owner.id, owner.build)
    return registry


def _plugin_data(definition: NotificationEventDefinition, data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in definition.variables:
        parts = key.split(".")
        value: Any = data
        for part in parts:
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError("Notification event variables must be scalar")
        if (
            (isinstance(value, str) and (len(value) > 2_000 or "\0" in value))
            or (isinstance(value, float) and not math.isfinite(value))
            or (isinstance(value, int) and value.bit_length() > 64)
        ):
            raise ValueError("Notification event variable exceeds limits")
        target = result
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return result


async def publish_event(
    session: AsyncSession,
    *,
    event_type: str,
    dedupe_key: str,
    resource_type: str,
    resource_id: str,
    actor_id: UUID | None,
    data: dict[str, Any],
    subject_id: UUID | None = None,
) -> bool:
    """Enqueue with the caller's transaction. Return false for disabled plugin events."""
    definition = BUILTIN_BY_ID.get(event_type)
    source_build = None
    if definition is None:
        site = await session.get(SiteSettings, 1)
        entry = event_registry(site.data if site else {}).get(event_type)
        if entry is None:
            if any(
                event.id == event_type
                for plugin in plugin_catalog().plugins
                for event in plugin.notification_events
            ):
                return False
            raise ValueError("Unknown notification event")
        definition, source_build = entry.definition, entry.build
        data = _plugin_data(definition, data)
    if definition.delivery_mode != "notification":
        raise ValueError("Authentication codes and recovery mail use their dedicated queue")
    if (definition.recipient_mode == "subject") != (subject_id is not None):
        raise ValueError("Notification event subject does not match its recipient policy")
    subject = await session.get(User, subject_id) if subject_id is not None else None
    if subject_id is not None and (subject is None or not subject.active):
        return False
    if not 0 < len(dedupe_key) <= 255:
        raise ValueError("Invalid notification deduplication key")
    if source_build:
        # Scope plugin keys so one plugin cannot reserve keys for another event.
        dedupe_key = f"{event_type}:{uuid5(NAMESPACE_URL, dedupe_key)}"
    result = await session.execute(
        insert(NotificationEvent)
        .values(
            id=uuid5(NAMESPACE_URL, f"everunfold:notification:{dedupe_key}"),
            event_type=event_type,
            event_version=1,
            dedupe_key=dedupe_key,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=actor_id,
            subject_id=subject_id,
            subject_email_verified_at=subject.email_verified_at if subject else None,
            source_build=source_build,
            data=data,
            occurred_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(NotificationEvent.id)
    )
    return result.scalar_one_or_none() is not None


async def publish_account_created(
    session: AsyncSession, event_type: str, user: User, actor_id: UUID
) -> None:
    await publish_event(
        session,
        event_type=event_type,
        dedupe_key=f"{event_type}:{user.id}",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=actor_id,
        data={
            "account": {"username": user.username, "display_name": user.display_name},
            "admin": {"url": f"/admin/users/{user.id}"},
        },
    )


async def publish_account_security(
    session: AsyncSession, event_type: str, user: User, occurrence: int | UUID
) -> None:
    await publish_event(
        session,
        event_type=event_type,
        dedupe_key=f"{event_type}:{user.id}:{occurrence}",
        resource_type="user",
        resource_id=str(user.id),
        actor_id=user.id,
        subject_id=user.id,
        data={"account": {"security_url": "/account"}},
    )


def subject_address_current(event: NotificationEvent, user: User) -> bool:
    return bool(
        event.subject_id == user.id
        and user.email_verified_at is not None
        and user.email_verified_at == event.subject_email_verified_at
    )


def recipient_event_data(event: NotificationEvent, recipient: User) -> dict[str, Any]:
    if event.event_type in ACCOUNT_EVENT_IDS and (
        recipient.must_change_password or user_permissions(recipient).get("users:manage") != "all"
    ):
        return {key: value for key, value in event.data.items() if key != "admin"}
    return event.data
