"""Short transactions for plan reconciliation and execution ownership."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs.definitions import definitions
from app.core.jobs.models import TaskPlan, TaskRun
from app.core.plugins.state import enabled_ids, installed_info
from app.modules.content.models import SiteSettings


def now() -> datetime:
    return datetime.now(UTC)


async def available(session: AsyncSession) -> set[str]:
    from app.core.plugins.loader import plugin_catalog

    site = await session.get(SiteSettings, 1)
    data = site.data if site else {}
    deployed = {item.id: item.build for item in installed_info(data)}
    enabled = enabled_ids(data)
    return {"core"} | {
        p.info.id
        for p in plugin_catalog().plugins
        if p.info.id in enabled and deployed.get(p.info.id) == p.info.build
    }


async def reconcile(session: AsyncSession) -> None:
    catalog = definitions()
    allowed = await available(session)
    for identity, (plugin, spec) in catalog.items():
        await session.execute(
            insert(TaskPlan)
            .values(id=identity, enabled=spec.enabled, next_at=now(), checkpoint={})
            .on_conflict_do_nothing()
        )
        plan = await session.scalar(
            select(TaskPlan).where(TaskPlan.id == identity).with_for_update()
        )
        assert plan is not None
        active = list(
            await session.scalars(
                select(TaskRun)
                .where(TaskRun.task_id == identity, TaskRun.status.in_(["queued", "running"]))
                .with_for_update()
            )
        )
        for run in active:
            if run.status == "running" and run.lease_until and run.lease_until < now():
                run.status, run.error, run.finished_at = "unknown", "execution_lease_expired", now()
                plan.next_at = max(plan.next_at, now() + timedelta(seconds=10))
            elif plugin not in allowed or not plan.enabled:
                if run.status == "queued":
                    run.status, run.finished_at = "cancelled", now()
        if plugin not in allowed or not plan.enabled or plan.next_at > now():
            continue
        # Registered executions recover; missed, unregistered ticks coalesce to one current scan.
        if not any(run.status in {"queued", "running"} for run in active):
            session.add(TaskRun(task_id=identity, planned_at=now()))
        plan.next_at = now() + timedelta(seconds=spec.interval)
    await session.commit()


async def claim(session: AsyncSession, identity: UUID, owner: str) -> TaskRun | None:
    run = await session.scalar(select(TaskRun).where(TaskRun.id == identity).with_for_update())
    if run is None or run.status != "queued":
        return None
    entry = definitions().get(run.task_id)
    plan = await session.get(TaskPlan, run.task_id)
    if not entry or entry[0] not in await available(session) or not plan or not plan.enabled:
        run.status, run.finished_at = "cancelled", now()
        await session.commit()
        return None
    run.status, run.owner, run.started_at = "running", owner, now()
    run.lease_until = now() + timedelta(
        seconds=20 if entry[1].kind == "resident" else entry[1].timeout + 15
    )
    await session.commit()
    return run
