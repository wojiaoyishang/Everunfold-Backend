"""Requeue the current settings, cache policy and content for a configured receiver."""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import AuthAuditEvent
from app.modules.content.publication import ContentPublication
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
