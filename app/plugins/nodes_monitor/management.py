"""Manage membership and queue repairs without moving a deployed credential."""

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import AuthAuditEvent
from app.modules.auth.service import AuthFailure
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.settings.models import SettingsDeliveryEvent
from app.modules.settings.node_registry import (
    MAX_NODES,
    deployment_directory,
    registry_directory,
    registry_record,
)
from app.modules.settings.nodes_schemas import NodeChange, NodeChangeResult
from app.modules.settings.service import SettingsService


async def revoke_leases(session: AsyncSession, node_id: str) -> None:
    await session.execute(
        update(SettingsDeliveryEvent)
        .where(
            SettingsDeliveryEvent.node_id == node_id,
            SettingsDeliveryEvent.applied_at.is_(None),
        )
        .values(lease_id=None, lease_until=None, error="node_inactive")
    )


async def manage_node(
    session: AsyncSession, settings: Settings, command: NodeChange, actor: UUID | None
) -> NodeChangeResult:
    await lock_references(session)
    service = SettingsService(session, settings)
    await service.site(locked=True)
    record = await registry_record(session, lock=True)
    directory = registry_directory(record) if record else deployment_directory(settings)
    if directory.revision != command.expected_revision:
        raise AuthFailure(409, 40926, "节点清单已更新，请刷新后重试")
    node_id = command.node_id
    previous = directory.entries.get(node_id)
    action = command.action
    if action == "add" and previous is not None:
        raise AuthFailure(409, 40927, "节点 ID 已存在")
    if action != "add" and previous is None:
        raise AuthFailure(404, 40420, "节点不存在")
    if action != "sync" and record is None:
        raise AuthFailure(409, 40928, "请先在后端服务器导入节点清单，启用在线管理")

    queued = action == "sync"
    if record is not None and action != "sync":
        entries = dict(record.nodes)
        if action == "delete":
            entries.pop(node_id)
            # A future node with the same ID must be explicitly bound again.
            record.bindings = {
                key: value for key, value in record.bindings.items() if key != node_id
            }
            await revoke_leases(session, node_id)
        else:
            value = command.registration
            assert value is not None
            if action == "add" and len(entries) >= MAX_NODES:
                raise AuthFailure(422, 42226, "节点数量已达上限")
            binding = directory.bindings.get(node_id)
            if value.enabled and binding is None:
                raise AuthFailure(409, 40929, "请先在后端服务器登记该节点的连接")
            entries[node_id] = value.model_dump()
            if not value.enabled:
                await revoke_leases(session, node_id)
            queued = bool(
                value.enabled
                and (previous is None or not previous.enabled)
                and binding
                and binding.settings_receiver_url
            )
        record.nodes = entries
        record.revision += 1
        await session.flush()

    if queued:
        await service.stage_retry(node_id, actor)
        await ContentPublication(session, settings).stage_retry(actor, node_id=node_id)
    revision = record.revision if record else 0
    session.add(
        AuthAuditEvent(
            user_id=actor,
            action="frontend_node_" + action,
            details={
                "node_id": node_id,
                "revision": revision,
                "actor": "deployment_console" if actor is None else "admin",
                "before": previous.model_dump() if previous else None,
                "after": command.registration.model_dump() if command.registration else None,
                "queued": queued,
            },
        )
    )
    await session.commit()
    return NodeChangeResult(revision=revision, queued=queued)
