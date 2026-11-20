"""Edit node membership and connection settings in one audited transaction."""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import AuthAuditEvent
from app.modules.auth.service import AuthFailure
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.settings.models import FrontendNodeRegistry, SettingsDeliveryEvent
from app.modules.settings.node_registry import (
    MAX_NODES,
    NodeBinding,
    deployment_directory,
    node_cipher,
    registration_bindings,
    registry_directory,
    registry_record,
)
from app.modules.settings.nodes_schemas import NodeChange, NodeChangeResult
from app.modules.settings.service import SettingsService

from .deployment_schemas import DeploymentNodeChange


async def revoke_leases(session: AsyncSession, node_id: str, reason: str = "node_inactive") -> None:
    await session.execute(
        update(SettingsDeliveryEvent)
        .where(
            SettingsDeliveryEvent.node_id == node_id,
            SettingsDeliveryEvent.applied_at.is_(None),
        )
        .values(lease_id=None, lease_until=None, error=reason)
    )


def edit_binding(
    settings: Settings,
    command: DeploymentNodeChange,
    bindings: dict[str, NodeBinding],
) -> NodeBinding | None:
    node = command.node_id
    previous = bindings.get(node)
    connection = command.connection
    credentials = command.credentials
    if previous is None:
        if not connection and not credentials and command.allowed_peer_ips is None:
            return None
        if not (connection and credentials and credentials.inbound and credentials.outbound):
            raise AuthFailure(422, 42226, "新配对需要填写内部接收器和两个方向的令牌")
        suffix = hashlib.sha256(node.encode()).hexdigest()[:16].upper()
        previous = NodeBinding(
            nextjs_to_fastapi_token_env=f"NEXTJS_TO_FASTAPI_NODE_{suffix}_TOKEN",
            fastapi_to_nextjs_token_env=f"FASTAPI_TO_NEXTJS_NODE_{suffix}_TOKEN",
        )
    updates = connection.model_dump() if connection is not None else {}
    if "allowed_peer_ips" in command.model_fields_set:
        updates["allowed_peer_ips"] = command.allowed_peer_ips
    if credentials is not None:
        inbound = previous.token(settings, node, "inbound")
        outbound = previous.token(settings, node, "outbound")
        new_inbound = credentials.inbound.get_secret_value() if credentials.inbound else inbound
        new_outbound = credentials.outbound.get_secret_value() if credentials.outbound else outbound
        if new_inbound and new_inbound == new_outbound:
            raise AuthFailure(422, 42226, "两个方向需要使用不同的令牌")
        changed = {
            value
            for old, value in ((inbound, new_inbound), (outbound, new_outbound))
            if value != old
        }
        if changed:
            for other, binding in bindings.items():
                if other != node and changed.intersection(
                    (
                        binding.token(settings, other, "inbound"),
                        binding.token(settings, other, "outbound"),
                    )
                ):
                    raise AuthFailure(422, 42226, "不同节点需要使用独立令牌")
        try:
            if new_inbound != inbound:
                updates["encrypted_inbound"] = (
                    node_cipher(settings, node, "inbound").encrypt(new_inbound.encode()).decode()
                )
            if new_outbound != outbound:
                updates["encrypted_outbound"] = (
                    node_cipher(settings, node, "outbound").encrypt(new_outbound.encode()).decode()
                )
        except ValueError:
            raise AuthFailure(503, 50326, "请先配置后端应用主密钥") from None
    return previous.model_copy(update=updates)


async def manage_node(
    session: AsyncSession, settings: Settings, command: NodeChange
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
    bindings = registration_bindings(directory, settings)
    previous_binding = bindings.get(node_id)
    binding = (
        edit_binding(settings, command, bindings)
        if isinstance(command, DeploymentNodeChange) and action in {"add", "edit"}
        else previous_binding
    )
    create_registry = record is None and (
        isinstance(command, DeploymentNodeChange)
        and action in {"add", "edit"}
        or action == "add"
        and not directory.entries
        and binding is not None
    )
    if create_registry:
        record = FrontendNodeRegistry(
            id=1,
            revision=0,
            nodes={node: entry.model_dump() for node, entry in directory.entries.items()},
            bindings={
                node: entry.model_dump(exclude_none=True)
                for node, entry in directory.bindings.items()
            },
        )
    delivery_changed = bool(
        binding is not None
        and (
            previous_binding is None
            or any(
                getattr(binding, field) != getattr(previous_binding, field)
                for field in ("settings_receiver_url", "allow_private_http", "encrypted_outbound")
            )
        )
    )
    if action != "sync" and record is None:
        raise AuthFailure(409, 40928, "请先在后端服务器导入节点清单，启用在线管理")

    queued = action == "sync"
    if record is not None and action != "sync":
        entries = dict(record.nodes)
        if action == "delete":
            entries.pop(node_id)
            # Reusing this ID requires an explicit registration or deployment import.
            record.bindings = {
                key: value for key, value in record.bindings.items() if key != node_id
            }
            await revoke_leases(session, node_id)
        else:
            value = command.registration
            assert value is not None
            # Publication belongs to the frontend. Preserve its last authenticated report
            # when the deployment form changes only internal pairing or enablement.
            if "base_url" not in value.model_fields_set and previous is not None:
                value = value.model_copy(update={"base_url": previous.base_url})
            if action == "add" and len(entries) >= MAX_NODES:
                raise AuthFailure(422, 42226, "节点数量已达上限")
            if value.enabled and binding is None:
                raise AuthFailure(409, 40929, "请先在后端服务器登记该节点的连接")
            if binding is not None and binding != directory.bindings.get(node_id):
                record.bindings = record.bindings | {node_id: binding.model_dump(exclude_none=True)}
            entries[node_id] = value.model_dump()
            if not value.enabled:
                await revoke_leases(session, node_id)
            elif delivery_changed and previous is not None and previous.enabled:
                await revoke_leases(session, node_id, "node_connection_changed")
            queued = bool(
                value.enabled
                and (previous is None or not previous.enabled or delivery_changed)
                and binding
                and binding.settings_receiver_url
            )
        record.nodes = entries
        record.revision += 1
        if create_registry:
            session.add(record)
        await session.flush()

    if queued:
        await service.stage_retry(node_id, None)
        await ContentPublication(session, settings).stage_retry(None, node_id=node_id)
    revision = record.revision if record else 0
    session.add(
        AuthAuditEvent(
            action="frontend_node_" + action,
            created_at=datetime.now(UTC),
            details={
                "node_id": node_id,
                "revision": revision,
                "actor": "deployment_console",
                "before": previous.model_dump() if previous else None,
                "after": record.nodes.get(node_id) if record and action != "sync" else None,
                "connection_changed": binding != previous_binding,
                "queued": queued,
            },
        )
    )
    await session.commit()
    return NodeChangeResult(revision=revision, queued=queued)
