"""Desired versions and verified delivery history shared by operations pages."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.models import SiteSettings
from app.modules.settings.models import (
    FrontendExtensionInventory,
    NodePolicy,
    SettingsDeliveryEvent,
)
from app.modules.settings.nodes_schemas import DeliverySummary, NodeStatus, Resource

RESOURCES: tuple[Resource, ...] = ("public-settings", "cache-policy", "public-content")


def display_error(error: str | None) -> str | None:
    if error == "node_inactive":
        return None
    if error in {
        "node_not_configured",
        "invalid_receipt",
        "source_epoch_changed",
        "delivery_unavailable",
    }:
        return error
    if error and error.startswith("receiver_http_"):
        suffix = error.removeprefix("receiver_http_")
        if len(suffix) == 3 and suffix.isascii() and suffix.isdecimal():
            return error
    return "delivery_failed" if error else None


async def read_history(
    session: AsyncSession,
    nodes: list[NodeStatus],
    sampled: datetime,
    attempt_limit: int,
) -> UUID | None:
    source = (
        await session.execute(
            select(
                SiteSettings.source_epoch,
                SiteSettings.generation,
                SiteSettings.content_generation,
            ).where(SiteSettings.id == 1)
        )
    ).one_or_none()
    if source is None:
        return None
    epoch, settings_rev, content_rev = source
    ids = [node.id for node in nodes]
    policies = dict(
        (
            await session.execute(
                select(NodePolicy.node_id, NodePolicy.revision).where(NodePolicy.node_id.in_(ids))
            )
        ).all()
    )
    reports = dict(
        (
            await session.execute(
                select(
                    FrontendExtensionInventory.node_id, FrontendExtensionInventory.reported_at
                ).where(FrontendExtensionInventory.node_id.in_(ids))
            )
        ).all()
    )
    event = SettingsDeliveryEvent
    scope = (event.source_epoch == epoch, event.node_id.in_(ids), event.resource.in_(RESOURCES))
    latest = {
        (node, resource): (revision, applied_at)
        for node, resource, revision, applied_at in (
            await session.execute(
                select(event.node_id, event.resource, event.revision, event.applied_at)
                .where(*scope, event.applied_at.is_not(None))
                .distinct(event.node_id, event.resource)
                .order_by(event.node_id, event.resource, event.revision.desc())
            )
        ).all()
    }
    current = {
        (node, resource): (attempts, retry_at, error, applied)
        for node, resource, attempts, retry_at, error, applied in (
            await session.execute(
                select(
                    event.node_id,
                    event.resource,
                    event.attempts,
                    event.next_attempt_at,
                    event.error,
                    event.applied_at.is_not(None),
                )
                .outerjoin(NodePolicy, NodePolicy.node_id == event.node_id)
                .where(
                    *scope,
                    event.revision
                    == case(
                        (event.resource == "public-settings", settings_rev),
                        (event.resource == "public-content", content_rev),
                        else_=func.coalesce(NodePolicy.revision, 1),
                    ),
                )
            )
        ).all()
    }
    for node in nodes:
        node.last_report = reports.get(node.id)
        if node.last_report is None:
            node.inbound = "never"
        else:
            node.inbound = (
                "recent" if (sampled - node.last_report).total_seconds() <= 150 else "stale"
            )
        for resource, expected in zip(
            RESOURCES,
            (settings_rev, policies.get(node.id, 1), content_rev),
            strict=True,
        ):
            acknowledged, applied_at = latest.get((node.id, resource), (None, None))
            entry = current.get((node.id, resource))
            attempts, retry_at, error, applied = entry or (0, None, None, False)
            state: Literal[
                "disabled", "not_configured", "unqueued", "pending", "retrying", "failed", "applied"
            ]
            if not node.enabled:
                state = "disabled"
            elif not node.receiver_configured:
                state = "not_configured"
            elif applied:
                state = "applied"
            elif entry is None:
                state = "unqueued"
            elif attempts >= attempt_limit and error:
                state = "failed"
            else:
                state = "retrying" if error else "pending"
            node.sync[resource] = DeliverySummary(
                expected=expected,
                acknowledged=acknowledged,
                acknowledged_at=applied_at,
                state=state,
                attempts=attempts,
                attempt_limit=attempt_limit,
                next_retry_at=retry_at if state == "retrying" else None,
                error=display_error(error),
            )
    return epoch
