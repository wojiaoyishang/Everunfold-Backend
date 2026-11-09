"""Execution-only imports and scheduler-owned resident child process lifecycle."""

import asyncio
import importlib
import os
from contextlib import suppress
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select

from app.core.config import Settings, load_settings
from app.core.database import Database
from app.core.jobs.definitions import definitions
from app.core.jobs.models import TaskPlan, TaskRun
from app.core.jobs.parameters import validate_parameters
from app.core.jobs.progress import progress_scope, report_progress, task_progress
from app.core.jobs.service import available, claim, now


def handler(path: str) -> Any:
    module, name = path.split(":")
    return getattr(importlib.import_module(module), name)


class ResidentContext:
    """Call checkpoint only after durable processing; external writes need their own idempotency."""

    def __init__(self, config: Settings, database: Database, run: TaskRun) -> None:
        self.config, self.database, self.run = config, database, run
        self.stopping = asyncio.Event()
        self.state = "connecting"
        self.last_message_at: str | None = None

    def connected(self) -> None:
        self.state = "connected"

    def received(self) -> None:
        self.last_message_at = now().isoformat()

    async def checkpoint(self, value: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self.database.session_factory() as session:
            await session.scalar(
                select(TaskPlan).where(TaskPlan.id == self.run.task_id).with_for_update()
            )
            row = await session.scalar(
                select(TaskRun).where(TaskRun.id == self.run.id).with_for_update()
            )
            if (
                not row
                or row.owner != self.run.owner
                or row.status != "running"
                or not row.lease_until
                or row.lease_until <= now()
            ):
                raise RuntimeError("Execution ownership lost")
            plan = await session.get(TaskPlan, row.task_id)
            assert plan
            if value is not None:
                import json

                if len(json.dumps(value)) > 65536:
                    raise ValueError("Checkpoint exceeds 64 KiB")
                plan.checkpoint = value
                await session.commit()
            return plan.checkpoint


async def execute(
    identity: str, *, resident: bool = False, parent_pid: int = 0, stop: Any = None
) -> None:
    config = load_settings()
    db = Database(config.database.url.get_secret_value())
    run: TaskRun | None = None
    owner = uuid4().hex
    result: dict[str, Any] = {}
    status, error = "succeeded", None
    try:
        async with db.session_factory() as session:
            run = await claim(session, UUID(identity), owner)
        if not run:
            return
        spec = definitions()[run.task_id][1]
        if (spec.kind == "resident") != resident:
            raise RuntimeError("Wrong execution role")
        with progress_scope(config, run.id, owner):
            with suppress(Exception):
                await task_progress(stage="running", message="Task execution started")
            if not resident:
                parameters = validate_parameters(spec, run.parameters)
                invocation = (
                    handler(spec.handler)(config, parameters)
                    if parameters is not None
                    else handler(spec.handler)(config)
                )
                result = await asyncio.wait_for(invocation, spec.timeout)
            else:
                ctx = ResidentContext(config, db, run)
                job = asyncio.create_task(handler(spec.handler)(ctx))
                try:
                    while not job.done():
                        async with db.session_factory() as session:
                            row = await session.scalar(
                                select(TaskRun).where(TaskRun.id == run.id).with_for_update()
                            )
                            plan = await session.get(TaskPlan, run.task_id)
                            owned = bool(
                                row
                                and row.status == "running"
                                and row.owner == owner
                                and row.lease_until
                                and row.lease_until > now()
                            )
                            enabled = bool(
                                plan
                                and plan.enabled
                                and definitions()[run.task_id][0] in await available(session)
                            )
                            if (
                                (stop and stop.is_set())
                                or os.getppid() != parent_pid
                                or not owned
                                or not enabled
                            ):
                                status = "stopped"
                                ctx.stopping.set()
                                break
                            assert row
                            row.lease_until = now() + timedelta(seconds=20)
                            row.result = {
                                "connection": ctx.state,
                                "last_message_at": ctx.last_message_at,
                                "pid": os.getpid(),
                                "parent_pid": parent_pid,
                            }
                            await session.commit()
                        await asyncio.wait({job}, timeout=3)
                    if ctx.stopping.is_set():
                        try:
                            await asyncio.wait_for(asyncio.shield(job), 5)
                        except TimeoutError:
                            job.cancel()
                    else:
                        await job
                    result = {"connection": "stopped", "last_message_at": ctx.last_message_at}
                finally:
                    if not job.done():
                        job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
    except Exception as exc:
        status, error = (
            "failed",
            type(exc).__name__,
        )  # Never expose exception messages or credentials.
    finally:
        if run:
            with suppress(Exception):
                await report_progress(
                    "runtime",
                    run.id,
                    config,
                    owner=owner,
                    stage=status,
                    level="error" if status == "failed" else "info",
                    message="Task execution " + status,
                )
            async with db.session_factory() as session:
                await session.scalar(
                    select(TaskPlan).where(TaskPlan.id == run.task_id).with_for_update()
                )
                row = await session.scalar(
                    select(TaskRun).where(TaskRun.id == run.id).with_for_update()
                )
                if (
                    row
                    and row.owner == owner
                    and row.status == "running"
                    and row.lease_until
                    and row.lease_until > now()
                ):
                    row.status, row.error, row.finished_at, row.result = (
                        status,
                        error,
                        now(),
                        result,
                    )
                    if resident and status == "failed":
                        history = list(
                            await session.scalars(
                                select(TaskRun.status)
                                .where(TaskRun.task_id == run.task_id, TaskRun.id != run.id)
                                .order_by(TaskRun.planned_at.desc())
                                .limit(7)
                            )
                        )
                        failures = 1
                        for previous in history:
                            if previous not in {"failed", "unknown"}:
                                break
                            failures += 1
                        plan = await session.get(TaskPlan, run.task_id)
                        if plan:
                            plan.next_at = now() + timedelta(
                                seconds=min(300, 15 * 2 ** (failures - 1))
                            )
                            if failures >= 8:
                                plan.enabled = False
                    await session.commit()
        await db.close()


def resident_entry(identity: str, parent_pid: int, stop: Any) -> None:
    asyncio.run(execute(identity, resident=True, parent_pid=parent_pid, stop=stop))
