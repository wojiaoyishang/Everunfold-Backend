"""Authenticated read/control for registered tasks only."""

import json
import time

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import select

from app.core.jobs.definitions import definitions
from app.core.jobs.models import TaskPlan, TaskRun
from app.core.jobs.schemas import (
    RuntimeCommand,
    RuntimeInstance,
    RuntimeOverview,
    RuntimePlan,
    RuntimeRun,
)
from app.core.jobs.service import available, now
from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicResponse
from app.modules.auth.service import AuthFailure
from app.modules.settings.models import SettingsAudit
from app.modules.settings.router import Authorized

router = APIRouter(prefix="/v1/admin/runtime", tags=["settings"])


async def overview(ctx: Authorized) -> RuntimeOverview:
    session = ctx.service.session
    allowed = await available(session)
    plans = {row.id: row for row in await session.scalars(select(TaskPlan))}
    runs = list(
        await session.scalars(select(TaskRun).order_by(TaskRun.planned_at.desc()).limit(100))
    )
    client = Redis.from_url(
        ctx.service.config.redis.url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
    )
    instances = []
    telemetry_available = True
    try:
        async for key in client.scan_iter(
            ctx.service.config.redis_prefix + "runtime:instance:*", count=100
        ):
            raw = await client.get(key)
            if raw:
                item = RuntimeInstance.model_validate(json.loads(raw))
                if item.state != "stopped" and time.time() - item.heartbeat > 20:
                    item.state = "stale"
                instances.append(item)
    except Exception:
        telemetry_available = False
    finally:
        await client.aclose()
    return RuntimeOverview(
        instances=instances,
        sampled_at=now(),
        telemetry_available=telemetry_available,
        plans=[
            RuntimePlan(
                id=key,
                title=spec.title,
                plugin=plugin,
                kind=spec.kind,
                enabled=plans[key].enabled if key in plans else spec.enabled,
                available=plugin in allowed,
                interval=spec.interval,
                manual=spec.manual,
                next_at=plans[key].next_at if key in plans else None,
            )
            for key, (plugin, spec) in definitions().items()
        ],
        runs=[
            RuntimeRun(
                id=str(row.id),
                task_id=row.task_id,
                status=row.status,
                planned_at=row.planned_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                error=row.error,
                connection=row.result.get("connection"),
                last_message_at=row.result.get("last_message_at"),
            )
            for row in runs
        ],
    )


@router.get("", response_model=PublicResponse[RuntimeOverview])
async def read_runtime(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await overview(ctx))


@router.post("/control", response_model=PublicResponse[RuntimeOverview])
async def control(payload: RuntimeCommand, ctx: Authorized) -> JSONResponseSuccess:
    session = ctx.service.session
    entry = definitions().get(payload.task_id)
    if not entry or entry[0] not in await available(session):
        raise AuthFailure(409, 40998, "任务未部署或插件未启用")
    plan = await session.scalar(
        select(TaskPlan).where(TaskPlan.id == payload.task_id).with_for_update()
    )
    if plan is None:
        raise AuthFailure(409, 40998, "请等待 Scheduler 登记任务")
    if payload.action == "run":
        if not entry[1].manual or not plan.enabled:
            raise AuthFailure(403, 40364, "此任务不允许手动执行")
        plan.next_at = now()
    else:
        plan.enabled = payload.action == "enable"
        if plan.enabled:
            plan.next_at = now()
    session.add(
        SettingsAudit(
            user_id=ctx.actor.id,
            resource="runtime-task",
            revision=1,
            changes={"task_id": plan.id, "action": payload.action},
        )
    )
    await session.commit()
    return JSONResponseSuccess(data=await overview(ctx))
