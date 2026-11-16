"""Read-only status assembled from delivery history, telemetry and node probes."""

import asyncio
import os
import time
from datetime import UTC, datetime
from uuid import UUID

import httpx2 as httpx
from pydantic import ValidationError

from app.core.config import FrontendConfig
from app.core.jobs.schemas import RuntimeInstance
from app.core.runtime import Runtime
from app.modules.settings.node_registry import (
    NodeDirectory,
    deployment_directory,
    read_directory,
    registration_bindings,
)
from app.modules.settings.nodes_schemas import (
    MonitorStatus,
    NodeStatus,
    ReceiverProbe,
    ReceiverStatus,
)
from app.modules.settings.sync_status import read_history


class DeploymentMonitorStatus(MonitorStatus):
    pending_pairings: list[str]


async def read_processes(runtime: Runtime) -> tuple[str, str]:
    status: dict[str, set[str]] = {"worker": set(), "scheduler": set()}
    cursor = 0
    for _ in range(10):
        cursor, keys = await runtime.redis.scan(
            cursor, match=runtime.settings.redis_prefix + "runtime:instance:*", count=100
        )
        if keys:
            for raw in await runtime.redis.mget(keys):
                if not raw:
                    continue
                try:
                    item = RuntimeInstance.model_validate_json(raw)
                except ValidationError:
                    continue
                if item.role in status:
                    value = (
                        "stale"
                        if time.time() - item.heartbeat > 20 or item.state == "stopped"
                        else "online"
                        if item.state == "online"
                        else "degraded"
                    )
                    status[item.role].add(value)
        if not cursor:
            break
    order = ("online", "degraded", "stale")
    unknown = "unavailable" if cursor else "never"
    return (
        next((value for value in order if value in status["worker"]), unknown),
        next((value for value in order if value in status["scheduler"]), unknown),
    )


async def probe_receiver(
    client: httpx.AsyncClient,
    node_id: str,
    target: FrontendConfig,
    epoch: UUID | None,
) -> ReceiverProbe:
    if not target.enabled:
        return ReceiverProbe(state="disabled")
    if not target.settings_receiver_url:
        return ReceiverProbe(state="not_configured")
    token = os.environ.get(target.fastapi_to_nextjs_token_env, "")
    if len(token) < 32:
        return ReceiverProbe(state="credential_missing")
    checked = datetime.now(UTC)
    try:
        async with client.stream(
            "GET",
            target.settings_receiver_url + "/internal/settings/status",
            headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code in (401, 403):
                return ReceiverProbe(state="rejected", checked_at=checked)
            if response.status_code in (404, 405):
                return ReceiverProbe(state="unsupported", checked_at=checked)
            if (
                response.status_code != 200
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                return ReceiverProbe(state="invalid", checked_at=checked)
            body = bytearray()
            async for chunk in response.aiter_raw():
                body.extend(chunk)
                if len(body) > 8192:
                    return ReceiverProbe(state="invalid", checked_at=checked)
        report = ReceiverStatus.model_validate_json(body)
    except (httpx.HTTPError, TimeoutError):
        return ReceiverProbe(state="unreachable", checked_at=checked)
    except ValueError:
        return ReceiverProbe(state="invalid", checked_at=checked)
    if report.node_id != node_id:
        return ReceiverProbe(state="wrong_node", checked_at=checked)
    if epoch is not None and report.source_epoch != str(epoch):
        return ReceiverProbe(state="wrong_source", checked_at=checked)
    return ReceiverProbe(
        state="reachable",
        checked_at=checked,
        replicas={
            "public-settings": report.public_settings,
            "cache-policy": report.cache_policy,
            "public-content": report.public_content,
        },
    )


async def read_status(runtime: Runtime, *, include_pairings: bool = False) -> MonitorStatus:
    sampled = datetime.now(UTC)
    timeout = runtime.settings.runtime.health_check_timeout_seconds
    revision = None
    database = True

    async def directory_status() -> NodeDirectory:
        async with runtime.database.session_factory() as session:
            return await read_directory(session, runtime.settings)

    try:
        directory = await asyncio.wait_for(directory_status(), timeout)
        revision = directory.revision
    except Exception:
        # Show deployment identities during outages. Their receiver addresses may be stale.
        database = False
        directory = deployment_directory(runtime.settings)
    targets = directory.targets
    nodes = [
        NodeStatus(
            id=node_id,
            enabled=target.enabled,
            base_url=target.base_url,
            bound=database and node_id in targets,
            receiver_configured=bool(
                database and node_id in targets and targets[node_id].settings_receiver_url
            ),
            receiver=ReceiverProbe(
                state="unavailable"
                if not database
                else "disabled"
                if not target.enabled
                else "not_configured"
            ),
        )
        for node_id, target in directory.entries.items()
    ]

    async def history_status() -> UUID | None:
        if not database:
            return None
        try:
            async with runtime.database.session_factory() as session:
                return await asyncio.wait_for(
                    read_history(session, nodes, sampled, runtime.settings.outbox.max_attempts),
                    timeout,
                )
        except Exception:
            return None

    async def process_status() -> tuple[bool, str, str]:
        try:
            await asyncio.wait_for(runtime.redis.ping(), timeout)
        except Exception:
            return False, "unavailable", "unavailable"
        try:
            worker, scheduler = await asyncio.wait_for(read_processes(runtime), timeout)
            return True, worker, scheduler
        except Exception:
            return True, "unavailable", "unavailable"

    epoch, (redis, worker, scheduler) = await asyncio.gather(
        history_status(),
        process_status(),
    )
    async with httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
        limits=httpx.Limits(max_connections=8),
    ) as client:

        async def check(node: NodeStatus) -> None:
            target = targets.get(node.id)
            if target is None or not database:
                return
            try:
                node.receiver = await asyncio.wait_for(
                    probe_receiver(client, node.id, target, epoch),
                    timeout,
                )
            except TimeoutError:
                node.receiver = ReceiverProbe(state="unreachable", checked_at=datetime.now(UTC))

        await asyncio.gather(*(check(node) for node in nodes))
    status = MonitorStatus(
        sampled_at=sampled,
        database="online" if database else "unavailable",
        redis="online" if redis else "unavailable",
        worker=worker,
        scheduler=scheduler,
        history_available=epoch is not None,
        registry_revision=revision,
        nodes=nodes,
    )
    if not include_pairings:
        return status
    pending = (
        sorted(registration_bindings(directory, runtime.settings).keys() - targets.keys())
        if revision is not None and (revision > 0 or not directory.entries)
        else []
    )
    return DeploymentMonitorStatus(**status.model_dump(), pending_pairings=pending)
