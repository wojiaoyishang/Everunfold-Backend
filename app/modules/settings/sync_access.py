"""A bounded, shared history of frontend-to-backend sync requests."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import Request
from pydantic import ValidationError
from redis.exceptions import RedisError
from starlette.requests import ClientDisconnect

from app.core.access_diagnostics import SyncBoundary, access_reason, peer_ip, safe_node_id
from app.core.runtime import Runtime
from app.core.schemas import PublicModel
from app.modules.auth.service import AuthFailure

SYNC_WINDOWS = frozenset({"15", "60", "360", "1440"})
TokenCheck = Literal["missing", "malformed", "unchecked", "matched", "mismatch"]
RETENTION_SECONDS = 24 * 3600
MAX_VISITS = 4000
MAX_GROUPS = 100

_REASONS = {
    "sync_queued": "同步已排队，等待 Worker 投递",
    "recovery_pending": "已有待投递任务，或仍在恢复冷却时间内",
    "inventory_registered": "扩展清单已登记",
    "receiver_missing": "节点尚未绑定同步接收器",
    "inventory_too_large": "扩展清单超过大小上限",
    "inventory_invalid": "扩展清单或前端发布 URL 无效",
    "registration_changed": "节点登记或配对发生变化，请刷新配置后重试",
    "client_disconnected": "客户端在请求完成前断开",
    "internal_error": "后端处理失败，请检查后端日志",
    "rejected": "后端拒绝处理，请按业务错误码检查后端日志",
}
_FAILURE_REASONS = {
    40320: "registration_changed",
    40420: "registration_changed",
    40925: "receiver_missing",
    41320: "inventory_too_large",
    42298: "inventory_invalid",
}


class SyncVisit(PublicModel):
    operation: SyncBoundary
    node_id: str
    peer_ip: str
    token_check: TokenCheck
    status: int = 200
    reason_code: str = ""
    error_code: int | None = None


class SyncVisitGroup(SyncVisit):
    latest_at: datetime
    count: int = 1
    reason: str


class SyncAccessReport(PublicModel):
    request_count: int
    accepted_count: int
    source_count: int
    group_count: int
    retention_limit: int
    retention_limit_reached: bool
    groups: list[SyncVisitGroup]


def token_check(header: str) -> TokenCheck:
    if not header or header == "Bearer ":
        return "missing"
    return "unchecked" if header.startswith("Bearer ") else "malformed"


def key(runtime: Runtime) -> str:
    return runtime.settings.redis_prefix + "nodes:sync-access:v1"


async def record(runtime: Runtime, visit: SyncVisit) -> None:
    if not runtime.settings.nodes_monitor.enabled:
        return
    now = time.time()
    member = uuid4().hex + ":" + visit.model_dump_json()
    name = key(runtime)
    try:
        async with asyncio.timeout(0.25), runtime.redis.pipeline(transaction=True) as pipe:
            pipe.zadd(name, {member: now})
            pipe.zremrangebyscore(name, "-inf", now - RETENTION_SECONDS)
            pipe.zremrangebyrank(name, 0, -MAX_VISITS - 1)
            pipe.expire(name, RETENTION_SECONDS)
            await pipe.execute()
    except (RedisError, TimeoutError):
        # Diagnostics must not block or change the outcome of a sync.
        return


@asynccontextmanager
async def observe_sync(
    runtime: Runtime, request: Request, node_id: str, operation: SyncBoundary
) -> AsyncIterator[SyncVisit]:
    visit = SyncVisit(
        operation=operation,
        node_id=safe_node_id(node_id),
        peer_ip=peer_ip(request),
        token_check=token_check(request.headers.get("authorization", "")),
    )
    try:
        yield visit
    except AuthFailure as exc:
        visit.status = exc.status
        visit.error_code = exc.code
        visit.reason_code = visit.reason_code or _FAILURE_REASONS.get(exc.code, "rejected")
        await record(runtime, visit)
        raise
    except ClientDisconnect:
        visit.status, visit.reason_code = 499, "client_disconnected"
        await record(runtime, visit)
        raise
    except Exception:
        visit.status, visit.reason_code = 500, "internal_error"
        await record(runtime, visit)
        raise
    else:
        await record(runtime, visit)


async def read_sync_access(runtime: Runtime, minutes: int) -> SyncAccessReport:
    now = time.time()
    async with asyncio.timeout(0.75):
        rows = await runtime.redis.zrevrangebyscore(
            key(runtime), now, now - minutes * 60, start=0, num=MAX_VISITS, withscores=True
        )
    groups: dict[bytes, SyncVisitGroup] = {}
    sources: set[str] = set()
    total = accepted = 0
    for member, score in rows:
        _, _, payload = member.partition(b":")
        try:
            visit = SyncVisit.model_validate_json(payload)
        except ValidationError:
            continue
        total += 1
        accepted += int(200 <= visit.status < 300)
        sources.add(visit.peer_ip)
        if group := groups.get(payload):
            group.count += 1
        else:
            groups[payload] = SyncVisitGroup(
                **visit.model_dump(),
                latest_at=datetime.fromtimestamp(score, UTC),
                reason=(
                    access_reason(visit.reason_code)
                    or _REASONS.get(visit.reason_code)
                    or "请检查后端日志"
                ),
            )
    return SyncAccessReport(
        request_count=total,
        accepted_count=accepted,
        source_count=len(sources),
        group_count=len(groups),
        retention_limit=MAX_VISITS,
        retention_limit_reached=len(rows) == MAX_VISITS,
        groups=list(groups.values())[:MAX_GROUPS],
    )
