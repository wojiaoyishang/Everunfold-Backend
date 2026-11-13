"""Dispatch notification events and deliver claimed platform messages."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from app.core.config import Settings
from app.core.database import Database
from app.core.plugins.loader import plugin_catalog
from app.core.plugins.state import enabled_ids, installed_info
from app.modules.auth.models import User
from app.modules.content.models import SiteSettings
from app.modules.notifications.events import event_registry, subject_address_current
from app.modules.notifications.models import NotificationDelivery, NotificationEvent
from app.modules.notifications.platforms import NotificationDeliveryRequest
from app.modules.notifications.routing import EMAIL
from app.modules.notifications.service import NotificationService
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles
from app.modules.settings.mail_transport import send_notification

MAX_ATTEMPTS = 5


async def deliver_notifications(config: Settings) -> dict[str, int]:
    database = Database(config.database.url.get_secret_value())
    dispatched = delivered = 0
    try:
        async with database.session_factory() as session:
            now = datetime.now(UTC)
            expired = (
                await session.scalars(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status == "sending",
                        NotificationDelivery.lease_until < now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for expired_row in expired:
                # A process may have stopped after an external side effect. Retrying
                # automatically could duplicate a message, so surface the uncertain
                # result for deliberate review instead.
                expired_row.status = "unknown"
                expired_row.error = "execution_lease_expired"
                expired_row.finished_at = now
                expired_row.lease_id = None
                expired_row.lease_until = None
            await session.commit()
        async with database.session_factory() as session:
            service = NotificationService(session, config)
            dispatched = await service.dispatch_events()
        for _ in range(20):
            now = datetime.now(UTC)
            lease = uuid4()
            async with database.session_factory() as session:
                candidate = await session.scalar(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.status == "pending",
                        NotificationDelivery.next_attempt_at <= now,
                        NotificationDelivery.attempts < MAX_ATTEMPTS,
                        or_(
                            NotificationDelivery.lease_until.is_(None),
                            NotificationDelivery.lease_until < now,
                        ),
                    )
                    .order_by(NotificationDelivery.next_attempt_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if candidate is None:
                    await session.commit()
                    break
                identity = candidate.id
                candidate.status = "sending"
                candidate.lease_id = lease
                candidate.lease_until = now + timedelta(seconds=120)
                candidate.attempts += 1
                await session.commit()

            status, error = await _deliver_one(config, identity, lease)
            async with database.session_factory() as session:
                claimed = await session.get(NotificationDelivery, identity, with_for_update=True)
                if claimed is None or claimed.lease_id != lease:
                    continue
                claimed.status = status
                claimed.error = error
                claimed.lease_id = None
                claimed.lease_until = None
                if status in {"sent", "suppressed"}:
                    claimed.finished_at = datetime.now(UTC)
                elif status == "failed" and claimed.attempts < MAX_ATTEMPTS:
                    claimed.next_attempt_at = datetime.now(UTC) + timedelta(
                        seconds=min(300, 2**claimed.attempts)
                    )
                else:
                    claimed.finished_at = datetime.now(UTC)
                await session.commit()
            delivered += 1
        return {"dispatched": dispatched, "delivered": delivered}
    finally:
        await database.close()


async def _deliver_one(config: Settings, identity: UUID, lease: UUID) -> tuple[str, str | None]:
    database = Database(config.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            row = await session.get(NotificationDelivery, identity)
            if row is None or row.lease_id != lease:
                return "suppressed", "lease_lost"
            event = await session.get(NotificationEvent, row.event_id)
            user = await session.get(User, row.user_id)
            site = await session.get(SiteSettings, 1)
            data = site.data if site else {}
            if event is None:
                return "suppressed", "event_unavailable"
            entry = event_registry(data).get(event.event_type)
            if (
                entry is None
                or entry.build != event.source_build
                or entry.definition.delivery_mode != "notification"
                or not entry.definition.accepts_platform(row.platform_id)
            ):
                return "suppressed", "event_unavailable"
            if user is None or not user.active:
                return "suppressed", "recipient_changed"
            if entry.definition.recipient_mode == "admins" and user.role != "admin":
                return "suppressed", "recipient_changed"
            if entry.definition.recipient_mode == "subject" and (
                user.id != event.subject_id
                or (row.platform_id == EMAIL and not subject_address_current(event, user))
            ):
                return "suppressed", "recipient_changed"
            if row.platform_id == EMAIL:
                revision, _ = stored_mail_profiles(data)
                profile = mail_profile(data, row.mail_profile_id)
                if (
                    (row.mail_config_revision is not None and revision != row.mail_config_revision)
                    or profile is None
                    or not profile.enabled
                    or not mail_ready(profile.value, profile.password)
                ):
                    if (
                        profile is not None
                        and row.mail_config_revision is not None
                        and revision != row.mail_config_revision
                    ):
                        return "suppressed", "mail_configuration_changed"
                    return "suppressed", "email_not_configured"
                value, password = profile.value, profile.password
                if not row.destination:
                    return "suppressed", "missing_recipient"
                if user.email != row.destination:
                    return "suppressed", "recipient_changed"
                if user.email_verified_at is None:
                    return "suppressed", "unverified_recipient"
                budget = min(120, max(15, value.timeout_seconds * 4)) + 5
                try:
                    status, error = await asyncio.wait_for(
                        asyncio.to_thread(
                            send_notification,
                            value,
                            password,
                            row.destination,
                            row.id,
                            row.subject,
                            row.text,
                            row.html,
                        ),
                        timeout=budget,
                    )
                except TimeoutError:
                    return "unknown", "delivery_unknown"
                if status == "sent":
                    return "sent", None
                if status == "unknown":
                    return "unknown", error
                return "failed", error

            catalog = plugin_catalog()
            plugin = catalog.notification_platform(row.platform_id)
            applied = {item.id: item for item in installed_info(data)}
            if plugin is None:
                return "suppressed", "platform_unavailable"
            owner = next(
                item
                for item in catalog.plugins
                if any(platform.id == row.platform_id for platform in item.notifications)
            )
            installed = applied.get(owner.info.id)
            if (
                owner.info.id not in enabled_ids(data)
                or installed is None
                or installed.build != owner.info.build
            ):
                return "suppressed", "platform_not_synced"
            try:
                result = await plugin.deliver(
                    NotificationDeliveryRequest(
                        delivery_id=row.id,
                        event_type=event.event_type,
                        event_data=event.data,
                        recipient_id=row.user_id,
                        recipient_name=user.display_name,
                        locale=row.locale,
                        subject=row.subject,
                        text=row.text,
                        href=row.href,
                    )
                )
            except Exception:
                # Platform failures can happen after an external request was sent.
                return "unknown", "platform_exception"
            if result.status == "unknown":
                return "unknown", result.error or "delivery_unknown"
            if result.status == "failed":
                return "failed", result.error or "delivery_failed"
            return "sent", None
    finally:
        await database.close()
