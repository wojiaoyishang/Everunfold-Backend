"""Requeue the current settings, cache policy and content for a configured receiver."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import AuthAuditEvent
from app.modules.content.publication import ContentPublication
from app.modules.settings.models import NodePolicy, SettingsDeliveryEvent
from app.modules.settings.service import SettingsService


async def stage_node_sync(
    session: AsyncSession,
    config: Settings,
    node_id: str,
    *,
    actor: Literal["deployment_cli", "frontend_node"],
) -> None:
    # Callers hold the reference lock and commit these events together.
    await SettingsService(session, config).stage_retry(node_id, None)
    await ContentPublication(session, config).stage_retry(None, node_id=node_id)
    session.add(
        AuthAuditEvent(
            action="frontend_node_sync",
            created_at=datetime.now(UTC),
            details={"node_id": node_id, "actor": actor, "queued": True},
        )
    )


async def stage_node_recovery(session: AsyncSession, config: Settings, node_id: str) -> bool:
    """Repair a missing frontend replica without overriding Worker retries or leases."""
    now = datetime.now(UTC)
    action = "frontend_node_recovery"
    # Callers hold the shared transaction lock. Limit even a misconfigured node
    # whose receiver keeps acknowledging while the site cannot use the replicas.
    if await session.scalar(
        select(AuthAuditEvent.id)
        .where(
            AuthAuditEvent.action == action,
            AuthAuditEvent.created_at >= now - timedelta(seconds=30),
            AuthAuditEvent.details["node_id"].astext == node_id,
        )
        .limit(1)
    ):
        return False
    service = SettingsService(session, config)
    site = await service.site(locked=True)
    policy = await session.get(NodePolicy, node_id)
    expected = {
        "public-content": site.content_generation,
        "public-settings": site.generation,
        "cache-policy": policy.revision if policy else 1,
    }
    event = SettingsDeliveryEvent
    pending = set(
        await session.scalars(
            select(event.resource)
            .where(
                event.node_id == node_id,
                event.source_epoch == site.source_epoch,
                event.applied_at.is_(None),
                or_(
                    *(
                        and_(event.resource == resource, event.revision == revision)
                        for resource, revision in expected.items()
                    )
                ),
            )
            .with_for_update()
        )
    )
    recover = expected.keys() - pending
    if "public-content" in recover:
        await ContentPublication(session, config).stage_retry(None, node_id=node_id)
    if "public-settings" in recover and "cache-policy" in recover:
        await service.stage_retry(node_id, None)
    elif "public-settings" in recover:
        await service.stage_retry(node_id, None, scope="public-settings")
    elif "cache-policy" in recover:
        await service.stage_retry(node_id, None, scope="cache-policy")
    if recover:
        session.add(
            AuthAuditEvent(
                action=action, created_at=now, details={"node_id": node_id, "queued": True}
            )
        )
    return bool(recover)
