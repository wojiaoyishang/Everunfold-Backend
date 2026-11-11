"""Leased outbox delivery. HTTP happens after the claim transaction commits."""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx2 as httpx
from sqlalchemy import or_, select

from app.core.config import Settings
from app.core.database import Database
from app.modules.content.models import SiteSettings
from app.modules.content.publication_schemas import ContentReceipt
from app.modules.notifications.service import publish_event
from app.modules.settings.models import SettingsDeliveryEvent
from app.modules.settings.schemas import DeliveryReceipt


async def deliver_batch(config: Settings) -> int:
    database = Database(config.database.url.get_secret_value())
    completed = 0
    try:
        from app.modules.content.timed_projection import refresh_opened_content

        async with database.session_factory() as session:
            await refresh_opened_content(session, config)
        for _ in range(config.outbox.batch_size):
            now = datetime.now(UTC)
            lease = uuid.uuid4()
            async with database.session_factory() as session:
                row = await session.scalar(
                    select(SettingsDeliveryEvent)
                    .where(
                        SettingsDeliveryEvent.applied_at.is_(None),
                        SettingsDeliveryEvent.next_attempt_at <= now,
                        SettingsDeliveryEvent.attempts < config.outbox.max_attempts,
                        or_(
                            SettingsDeliveryEvent.lease_until.is_(None),
                            SettingsDeliveryEvent.lease_until < now,
                        ),
                    )
                    .order_by(SettingsDeliveryEvent.next_attempt_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    break
                event_id, payload, node_id = row.id, row.payload, row.node_id
                current_epoch = await session.scalar(
                    select(SiteSettings.source_epoch).where(SiteSettings.id == 1)
                )
                if current_epoch != row.source_epoch:
                    row.error = "source_epoch_changed"
                    row.attempts = config.outbox.max_attempts
                    await session.commit()
                    continue
                row.lease_id = lease
                row.lease_until = now + timedelta(
                    seconds=max(
                        config.outbox.lease_seconds, config.outbox.request_timeout_seconds + 10
                    )
                )
                row.attempts += 1
                await session.commit()
            error: str | None = None
            target = config.frontends.get(node_id)
            secret = os.environ.get(target.fastapi_to_nextjs_token_env, "") if target else ""
            if (
                not target
                or not target.enabled
                or not target.settings_receiver_url
                or len(secret) < 32
            ):
                error = "node_not_configured"
            else:
                encoded = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
                expected_digest = hashlib.sha256(encoded).hexdigest()
                try:
                    async with httpx.AsyncClient(
                        follow_redirects=False,
                        trust_env=False,
                        timeout=httpx.Timeout(
                            config.outbox.request_timeout_seconds,
                            connect=config.outbox.connect_timeout_seconds,
                        ),
                    ) as client:
                        response = await client.post(
                            target.settings_receiver_url
                            + (
                                "/internal/cache/v1/apply"
                                if payload["resource"] == "public-content"
                                else "/internal/settings/apply"
                            ),
                            content=encoded,
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": "Bearer " + secret,
                            },
                        )
                    if response.status_code != 200 or len(response.content) > 8192:
                        error = f"receiver_http_{response.status_code}"
                    else:
                        receipt = (
                            ContentReceipt.model_validate(response.json())
                            if payload["resource"] == "public-content"
                            else DeliveryReceipt.model_validate(response.json())
                        )
                        if (
                            receipt.node_id != node_id
                            or receipt.source_epoch != payload["source_epoch"]
                            or receipt.resource != payload["resource"]
                            or receipt.revision != payload["revision"]
                            or receipt.digest != expected_digest
                        ):
                            error = "invalid_receipt"
                except (httpx.TransportError, ValueError, KeyError):
                    error = "delivery_unavailable"
            async with database.session_factory() as session:
                row = await session.get(SettingsDeliveryEvent, event_id, with_for_update=True)
                # A timed-out worker may not acknowledge a new owner's lease.
                if row is None or row.lease_id != lease:
                    continue
                row.lease_id = None
                row.lease_until = None
                row.error = error
                if error is None:
                    row.applied_at = datetime.now(UTC)
                    completed += 1
                else:
                    row.next_attempt_at = datetime.now(UTC) + timedelta(
                        seconds=min(
                            2 ** min(row.attempts, 16), config.outbox.retry_max_delay_seconds
                        )
                    )
                    if row.attempts >= config.outbox.max_attempts:
                        await publish_event(
                            session,
                            event_type="settings.delivery_failed",
                            dedupe_key=f"settings-delivery-failed:{row.id}",
                            resource_type="settings-delivery",
                            resource_id=str(row.id),
                            actor_id=None,
                            data={
                                "node": {"id": row.node_id},
                                "settings": {"resource": row.payload["resource"]},
                                "failure": {"reason": error},
                                "admin": {"url": "/admin/settings"},
                            },
                        )
                await session.commit()
        return completed
    finally:
        await database.close()
