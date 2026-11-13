"""Commit cache changes with the public version and dispatch them from the Worker."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database
from app.core.plugins.loader import plugin_catalog
from app.core.plugins.state import cache_event_subscribers, installed_info
from app.modules.content.models import SiteSettings
from app.modules.settings.cache_event_schema import (
    CacheChange,
    CacheEvent,
    CacheEventHandler,
    CacheResource,
)
from app.modules.settings.models import CacheEventDelivery, CacheEventRecord


async def stage_cache_events(
    session: AsyncSession,
    site: SiteSettings,
    resource: CacheResource,
    revision: int,
    changes: tuple[CacheChange, ...],
    *,
    node_id: str | None = None,
) -> None:
    # Use the committed catalog so API replicas on different builds stage the same deliveries.
    subscribers = cache_event_subscribers(site.data)
    now = datetime.now(UTC)
    for change in changes:
        identity = uuid5(
            NAMESPACE_URL,
            f"everunfold:cache:{site.source_epoch}:{resource}:{revision}:"
            f"{node_id}:{change.reason}:{change.content_id}",
        )
        event = CacheEvent(
            id=identity,
            source_epoch=site.source_epoch,
            resource=resource,
            revision=revision,
            occurred_at=now,
            reason=change.reason,
            node_id=node_id,
            content_id=change.content_id,
            paths=tuple(dict.fromkeys(change.paths)),
        )
        await session.execute(
            insert(CacheEventRecord)
            .values(id=identity, data=event.model_dump(mode="json"))
            .on_conflict_do_nothing()
        )
        if subscribers:
            await session.execute(
                insert(CacheEventDelivery)
                .values(
                    [
                        {"event_id": identity, "plugin_id": plugin, "available_at": now}
                        for plugin in subscribers
                    ]
                )
                .on_conflict_do_nothing()
            )


@dataclass(frozen=True)
class Claim:
    event: CacheEvent
    plugin_id: str
    lease: UUID
    handler: CacheEventHandler


async def claim_delivery(session: AsyncSession, config: Settings) -> tuple[bool, Claim | None]:
    now = datetime.now(UTC)
    row = await session.scalar(
        select(CacheEventDelivery)
        .where(
            CacheEventDelivery.status == "pending",
            CacheEventDelivery.available_at <= now,
            or_(CacheEventDelivery.lease_until.is_(None), CacheEventDelivery.lease_until <= now),
        )
        .order_by(
            CacheEventDelivery.available_at,
            CacheEventDelivery.event_id,
            CacheEventDelivery.plugin_id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return False, None
    record = await session.get(CacheEventRecord, row.event_id)
    assert record is not None
    event = CacheEvent.model_validate(record.data)
    site = await session.get(SiteSettings, 1)
    plugin = next((p for p in plugin_catalog().plugins if p.info.id == row.plugin_id), None)
    if (
        site is None
        or site.source_epoch != event.source_epoch
        or row.plugin_id not in cache_event_subscribers(site.data)
    ):
        row.status, row.error = "skipped", "inactive_source_or_plugin"
    elif row.attempts >= config.outbox.max_attempts:
        row.status, row.error = "failed", "attempts_exhausted"
    elif (
        plugin is None
        or plugin.cache_event_handler is None
        or not any(
            p.id == plugin.info.id and p.build == plugin.info.build
            for p in installed_info(site.data)
        )
    ):
        # A Worker may still be on the previous plugin build during a rolling update.
        row.available_at = now + timedelta(seconds=60)
        row.error = "plugin_build_unavailable"
    else:
        lease = uuid4()
        row.attempts += 1
        row.lease_id = lease
        row.lease_until = now + timedelta(seconds=30)
        await session.commit()
        return True, Claim(event, row.plugin_id, lease, plugin.cache_event_handler)
    row.lease_id = row.lease_until = None
    await session.commit()
    return True, None


async def deliver_cache_events(config: Settings) -> dict[str, int]:
    database = Database(config.database.url.get_secret_value())
    completed = 0
    try:
        for _ in range(min(config.outbox.batch_size, 8)):
            async with database.session() as session:
                found, claim = await claim_delivery(session, config)
            if not found:
                break
            if claim is None:
                continue
            error = None
            try:
                async with asyncio.timeout(10):
                    await claim.handler(claim.event)
            except TimeoutError:
                error = "handler_timeout"
            except Exception:
                error = "handler_failed"
            async with database.session() as session:
                row = await session.get(
                    CacheEventDelivery, (claim.event.id, claim.plugin_id), with_for_update=True
                )
                if row is None or row.lease_id != claim.lease:
                    continue
                row.lease_id = row.lease_until = None
                row.error = error
                if error is None:
                    row.status = "delivered"
                    completed += 1
                elif row.attempts >= config.outbox.max_attempts:
                    row.status = "failed"
                else:
                    row.available_at = datetime.now(UTC) + timedelta(
                        seconds=min(
                            2 ** min(row.attempts, 16), config.outbox.retry_max_delay_seconds
                        )
                    )
                await session.commit()
        return {"completed": completed}
    finally:
        await database.close()
