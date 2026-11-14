"""Bounded, read-only health diagnostics with deployment details kept private."""

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.jobs.schemas import RuntimeInstance
from app.core.runtime import Runtime
from app.modules.content.models import SiteSettings
from app.modules.settings.mail_service import default_mail_profile, mail_ready, stored_mail_profiles

from .schemas import MaintenanceHealth, MaintenanceHealthCheck

CheckName = Literal["database", "redis", "queue", "media", "mail", "query_cache"]
Status = Literal["ok", "warning", "unavailable", "not_configured"]
Outcome = tuple[Status, str | None]


async def _database(runtime: Runtime) -> Outcome:
    await runtime.database.ping()
    if await runtime.database.schema_is_current():
        return "ok", "数据库可连接，表结构版本正确。"
    return "warning", "数据库可连接，表结构需要更新。"


async def _redis(runtime: Runtime) -> Outcome:
    return ("ok", None) if await runtime.redis.ping() else ("unavailable", None)


async def _queue(runtime: Runtime) -> Outcome:
    settings = runtime.settings
    timeout = settings.runtime.health_check_timeout_seconds
    async with Redis.from_url(
        settings.celery.broker_url.get_secret_value(),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    ) as broker:
        if not await broker.ping():
            return "unavailable", "无法连接任务队列。"
    roles: set[str] = set()
    now = time.time()
    examined = 0
    async for key in runtime.redis.scan_iter(
        match=settings.redis_prefix + "runtime:instance:*", count=100
    ):
        raw = await runtime.redis.get(key)
        if raw:
            worker = RuntimeInstance.model_validate(json.loads(raw))
            if worker.state == "online" and now - worker.heartbeat <= 20:
                roles.add(worker.role)
        examined += 1
        if examined >= 100 or {"worker", "scheduler"} <= roles:
            break
    if {"worker", "scheduler"} <= roles:
        return "ok", "队列可连接，Worker 和调度器均有近期心跳。"
    if "worker" not in roles and "scheduler" not in roles:
        return "warning", "队列可连接，暂无 Worker 或调度器近期心跳。"
    if "worker" not in roles:
        return "warning", "队列可连接，暂无 Worker 近期心跳。"
    return "warning", "队列可连接，暂无调度器近期心跳。"


async def _media(config: Settings) -> Outcome:
    def shared_storage_access() -> bool:
        directory = config.uploads_directory
        return directory.is_dir() and os.access(directory, os.R_OK | os.W_OK | os.X_OK)

    if await asyncio.to_thread(shared_storage_access):
        return "ok", "媒体目录可访问。"
    return "unavailable", "媒体目录不存在或无法读写。"


async def _mail(session: AsyncSession) -> Outcome:
    site = await session.get(SiteSettings, 1)
    data = site.data if site else {}
    _, profiles = stored_mail_profiles(data)
    active = default_mail_profile(data)
    if not profiles:
        return "not_configured", "尚未配置发信资料。"
    if active and active.enabled and mail_ready(active.value, active.password):
        return "ok", "默认发信配置完整。可在发信设置中验证投递。"
    return "warning", "默认发信配置待完成或启用。"


async def _query_cache(runtime: Runtime) -> Outcome:
    status = await runtime.query_cache.status()
    if status in {"disabled", "unconfigured"}:
        return "not_configured", "查询使用数据库。"
    if status == "ready":
        return "ok", "查询缓存可连接。"
    return "unavailable", "查询缓存不可用，查询回退到数据库。"


async def health_report(runtime: Runtime, session: AsyncSession) -> MaintenanceHealth:
    timeout = runtime.settings.runtime.health_check_timeout_seconds

    async def check(
        identity: CheckName, probe: Callable[[], Awaitable[Outcome]]
    ) -> MaintenanceHealthCheck:
        try:
            status, detail = await asyncio.wait_for(probe(), timeout)
        except Exception:
            status, detail = "unavailable", "检查暂不可用。"
        return MaintenanceHealthCheck(id=identity, status=status, detail=detail)

    return MaintenanceHealth(
        checked_at=datetime.now(UTC),
        checks=list(
            await asyncio.gather(
                check("database", lambda: _database(runtime)),
                check("redis", lambda: _redis(runtime)),
                check("queue", lambda: _queue(runtime)),
                check("media", lambda: _media(runtime.settings)),
                check("mail", lambda: _mail(session)),
                check("query_cache", lambda: _query_cache(runtime)),
            )
        ),
    )
