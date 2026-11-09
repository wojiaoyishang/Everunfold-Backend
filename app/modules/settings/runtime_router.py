"""Authenticated read/control for registered tasks only."""

import json
import time
from typing import Any

from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.core.jobs.definitions import definitions
from app.core.jobs.models import TaskPlan, TaskRun
from app.core.jobs.parameter_metadata import public_parameters
from app.core.jobs.parameters import checked_json, normalized_parameters, parameters_schema
from app.core.jobs.schemas import (
    RuntimeCommand,
    RuntimeInstance,
    RuntimeOverview,
    RuntimePlan,
    RuntimeRun,
)
from app.core.jobs.service import available, now
from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.service import AuthFailure
from app.modules.settings.models import SettingsAudit
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/runtime",
    tags=["settings"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)


async def overview(ctx: Authorized) -> RuntimeOverview:
    session = ctx.service.session
    allowed = await available(session)
    plans = {row.id: row for row in await session.scalars(select(TaskPlan))}
    runs = list(
        await session.scalars(
            select(TaskRun)
            .order_by(TaskRun.status.in_(["queued", "running"]).desc(), TaskRun.planned_at.desc())
            .limit(100)
        )
    )
    client = Redis.from_url(
        ctx.service.config.redis.url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
    )
    resident_ids = [key for key, (_, spec) in definitions().items() if spec.kind == "resident"]
    counts = dict(
        (
            await session.execute(
                select(TaskRun.task_id, func.count())
                .where(TaskRun.task_id.in_(resident_ids))
                .group_by(TaskRun.task_id)
            )
        ).all()
    )
    queue_depth = None
    instances = []
    telemetry_available = True
    try:
        queue = (
            ctx.service.config.redis_prefix
            + "celery:"
            + ctx.service.config.redis_prefix
            + "settings"
        )
        queue_depth = sum(
            [
                await client.llen(queue + suffix)
                for suffix in ("", "\x06\x163", "\x06\x166", "\x06\x169")
            ]
        )
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
    descriptions = {
        key: public_parameters(parameters_schema(spec), spec.default_parameters)
        for key, (_, spec) in definitions().items()
    }
    return RuntimeOverview(
        instances=instances,
        queue_depth=queue_depth,
        sampled_at=now(),
        telemetry_available=telemetry_available,
        plans=[
            RuntimePlan(
                id=key,
                restarts=max(0, counts.get(key, 0) - 1),
                title=spec.title,
                handler=spec.handler,
                parameters_schema=descriptions[key][0],
                parameter_defaults=descriptions[key][1],
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
    audit: dict[str, Any] = {"task_id": plan.id, "action": payload.action}
    if payload.action == "debug_run":
        if entry[1].kind != "periodic":
            raise AuthFailure(403, 40364, "开发测试只支持 Worker 有限任务")
        if not plan.enabled:
            raise AuthFailure(403, 40364, "任务计划已停用，请先明确启用后测试")
        try:
            parameters = normalized_parameters(entry[1], payload.parameters)
        except ValueError:
            raise AuthFailure(
                422, 42299, "任务参数不符合声明，请检查类型、必填字段和范围"
            ) from None
        active = await session.scalar(
            select(TaskRun)
            .where(TaskRun.task_id == plan.id, TaskRun.status.in_(["queued", "running"]))
            .order_by(TaskRun.planned_at)
            .limit(1)
        )
        reused = active is not None
        if active is not None and checked_json(active.parameters) != checked_json(parameters):
            raise AuthFailure(409, 40998, "已有使用不同参数的排队或运行任务，请等待完成后再触发")
        if active is None:
            active = TaskRun(task_id=plan.id, planned_at=now(), parameters=parameters)
            session.add(active)
            await session.flush()
        audit.update(
            {"run_id": str(active.id), "reused": reused, "parameter_keys": sorted(parameters)}
        )
    elif payload.action == "run":
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
            changes=audit,
        )
    )
    await session.commit()
    return JSONResponseSuccess(data=await overview(ctx))
