"""Cache recovery must preserve active retries and replace invalid acknowledgments."""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import FrontendConfig, Settings
from app.modules.auth.permissions import Actor
from app.modules.media.locking import lock_references
from app.modules.settings import cache_router
from app.modules.settings.cache_schemas import CacheClearReceipt
from app.modules.settings.manual_sync import stage_node_recovery, stage_node_sync
from app.modules.settings.models import FrontendNodeRegistry, SettingsDeliveryEvent
from app.modules.settings.router import Access
from app.modules.settings.service import SettingsService
from tests import test_public_content_integration

anyio_backend = test_public_content_integration.anyio_backend
database_session = test_public_content_integration.database_session
isolated_settings = test_public_content_integration.isolated_settings

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Dedicated PostgreSQL test database"
    ),
]


async def test_clear_requeues_acknowledged_replica_and_recovery_leaves_active_delivery(
    database_session: AsyncSession,
    isolated_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = database_session
    node_id = f"cache-test-{uuid4().hex}"
    settings = isolated_settings.model_copy(
        update={
            "frontends": {
                node_id: FrontendConfig(
                    enabled=True,
                    settings_receiver_url="http://127.0.0.1:3000",
                    nextjs_to_fastapi_token_env="TEST_CACHE_NODE_TOKEN",
                    fastapi_to_nextjs_token_env="TEST_CACHE_REVERSE_TOKEN",
                )
            }
        }
    )
    await lock_references(session)
    await session.execute(delete(FrontendNodeRegistry))
    await stage_node_sync(session, settings, node_id, actor="deployment_cli")
    events = {
        event.resource: event
        for event in await session.scalars(
            select(SettingsDeliveryEvent).where(SettingsDeliveryEvent.node_id == node_id)
        )
    }
    now = datetime.now(UTC)
    for event in events.values():
        event.applied_at = now
    pending = events["cache-policy"]
    lease, retry_at = uuid4(), now + timedelta(minutes=5)
    pending.applied_at = None
    pending.attempts = settings.outbox.max_attempts
    pending.lease_id = lease
    pending.next_attempt_at = retry_at
    pending.error = "delivery_unavailable"

    assert await stage_node_recovery(session, settings, node_id)
    assert events["public-content"].applied_at is None
    assert events["public-settings"].applied_at is None
    assert pending.lease_id == lease and pending.next_attempt_at == retry_at
    assert pending.attempts == settings.outbox.max_attempts
    assert not await stage_node_recovery(session, settings, node_id)

    for event in events.values():
        event.applied_at = now
    service = SettingsService(session, settings)
    site = await service.site()
    receipt = CacheClearReceipt(
        protocol="cache-clear-1",
        node_id=node_id,
        source_epoch=str(site.source_epoch),
        removed_files=3,
        removed_bytes=100,
    )
    receive = AsyncMock(return_value=receipt)
    monkeypatch.setattr(cache_router, "auth_runtime", lambda request: object())
    monkeypatch.setattr(cache_router, "receiver_request", receive)
    ctx = Access(service, Actor(uuid4(), "test-admin", "Test Admin", "admin", {}))
    response = await cache_router.clear_cache(node_id, Request({"type": "http"}), ctx)
    assert response.status_code == 200
    receive.assert_awaited_once()
    assert all(event.applied_at is None and event.attempts == 0 for event in events.values())
    assert pending.lease_id is None and pending.error is None
