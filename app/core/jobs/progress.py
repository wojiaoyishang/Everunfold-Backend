"""Durable progress reporting and resumable reads, fenced by execution ownership."""

import asyncio
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select

from app.core.config import Settings
from app.core.database import Database
from app.core.jobs.models import TaskRun
from app.core.jobs.progress_models import TaskProgressEventRecord as Event
from app.core.jobs.progress_schemas import (
    Kind,
    Level,
    Resource,
    State,
    TaskProgress,
    TaskProgressEvent,
    WatchTask,
)
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.settings.models import MailTestDelivery

logger = logging.getLogger(__name__)
MAX_EVENTS = 1000
_scope: ContextVar[tuple[Settings, UUID, str] | None] = ContextVar(
    "task_progress_scope", default=None
)


@contextmanager
def progress_scope(config: Settings, identity: UUID, owner: str) -> Iterator[None]:
    token = _scope.set((config, identity, owner))
    try:
        yield
    finally:
        _scope.reset(token)


async def task_progress(**values: Any) -> bool:
    scope = _scope.get()
    if scope is None:
        return False
    config, identity, owner = scope
    return await report_progress("runtime", identity, config, owner=owner, **values)


async def report_progress(
    resource: Resource,
    identity: UUID,
    config: Settings,
    *,
    stage: str,
    message: str,
    completed: float | None = None,
    total: float | None = None,
    unit: str | None = None,
    level: Level = "info",
    kind: Kind = "stage",
    owner: str | None = None,
    expected_lease: datetime | None = None,
) -> bool:
    """Messages must be safe for the requesting user; never pass credentials/raw exceptions."""
    if (
        not stage
        or len(stage) > 80
        or level not in {"info", "warning", "error"}
        or kind not in {"stage", "request", "response", "message"}
    ):
        raise ValueError("Invalid progress event")
    if any(
        value is not None and (not math.isfinite(value) or value < 0)
        for value in (completed, total)
    ):
        raise ValueError("Progress counts must be finite and nonnegative")
    if unit is not None and len(unit) > 40:
        raise ValueError("Progress unit is too long")
    db = Database(config.database.url.get_secret_value())
    try:
        async with db.session_factory() as session:
            model = TaskRun if resource == "runtime" else MailTestDelivery
            row = await session.scalar(select(model).where(model.id == identity).with_for_update())
            now = datetime.now(UTC)
            if row is None:
                return False
            if resource == "runtime":
                if (
                    not isinstance(row, TaskRun)
                    or not owner
                    or row.owner != owner
                    or row.status != "running"
                    or row.lease_until is None
                    or row.lease_until <= now
                ):
                    return False
            elif isinstance(row, MailTestDelivery):
                if expected_lease is not None and (
                    row.status != "sending" or row.lease_until != expected_lease
                ):
                    return False
                if row.status not in {"queued", "sending"} or (
                    row.status == "sending" and (row.lease_until is None or row.lease_until <= now)
                ):
                    return False
            else:
                return False
            # The resource row lock serializes sequence allocation/commit for this resource.
            session.add(
                Event(
                    resource=resource,
                    resource_id=identity,
                    at=now,
                    level=level,
                    kind=kind,
                    message=message[:8192],
                    stage=stage,
                    completed=completed,
                    total=total,
                    unit=unit,
                )
            )
            await session.flush()
            old = (
                select(Event.sequence)
                .where(Event.resource == resource, Event.resource_id == identity)
                .order_by(Event.sequence.desc())
                .offset(MAX_EVENTS)
            )
            await session.execute(delete(Event).where(Event.sequence.in_(old)))
            await session.commit()
            return True
    except Exception as exc:
        # Diagnostics are best effort; their failure must not retry a completed side effect.
        logger.warning("Task progress persistence unavailable (%s)", type(exc).__name__)
        return False
    finally:
        with suppress(Exception):
            await db.close()


async def _snapshot(db: Database, actor: Actor, query: WatchTask) -> TaskProgress:
    capability = "settings:read" if query.resource == "runtime" else "settings:write"
    if not actor.has(capability):
        raise AuthFailure(403, 40364, "没有读取任务进度的权限")
    async with db.session_factory() as session:
        model = TaskRun if query.resource == "runtime" else MailTestDelivery
        row = await session.get(model, query.id)
        if not isinstance(row, (TaskRun, MailTestDelivery)) or (
            isinstance(row, MailTestDelivery) and row.user_id != actor.id
        ):
            raise AuthFailure(404, 40499, "任务不存在或不可访问")
        now = datetime.now(UTC)
        state = {"sending": "running", "sent": "succeeded", "stopped": "cancelled"}.get(
            row.status, row.status
        )
        if state not in {"queued", "running", "succeeded", "failed", "unknown", "cancelled"}:
            state = "unknown"
        if state == "running" and row.lease_until is not None and row.lease_until <= now:
            state = "unknown"
        if (
            isinstance(row, MailTestDelivery)
            and row.status == "queued"
            and row.created_at < now - timedelta(minutes=10)
        ):
            state = "failed"
        where = (Event.resource == query.resource, Event.resource_id == query.id)
        events = list(
            await session.scalars(
                select(Event)
                .where(*where, Event.sequence > query.after_sequence)
                .order_by(Event.sequence)
                .limit(101)
            )
        )
        latest = await session.scalar(
            select(Event).where(*where).order_by(Event.sequence.desc()).limit(1)
        )
        gauge = await session.scalar(
            select(Event)
            .where(*where, Event.completed.is_not(None))
            .order_by(Event.sequence.desc())
            .limit(1)
        )
        started = row.started_at if isinstance(row, TaskRun) else None
        if isinstance(row, MailTestDelivery):
            first = await session.scalar(
                select(Event)
                .where(*where, Event.stage != "queued")
                .order_by(Event.sequence)
                .limit(1)
            )
            started = first.at if first else None
        created = row.planned_at if isinstance(row, TaskRun) else row.created_at
        page = events[:100]
        percent = (
            min(100.0, gauge.completed / gauge.total * 100)
            if gauge and gauge.total and gauge.completed is not None
            else None
        )
        return TaskProgress(
            resource=query.resource,
            id=query.id,
            state=cast(State, state),
            stage=latest.stage if latest and state in {"queued", "running"} else state,
            completed=gauge.completed if gauge else None,
            total=gauge.total if gauge else None,
            unit=gauge.unit if gauge else None,
            percent=percent,
            updated_at=row.finished_at or (latest.at if latest else started or created),
            started_at=started,
            finished_at=row.finished_at,
            cursor=page[-1].sequence if page else query.after_sequence,
            has_more=len(events) > 100,
            events=[
                TaskProgressEvent(
                    sequence=e.sequence,
                    at=e.at,
                    level=cast(Level, e.level),
                    kind=cast(Kind, e.kind),
                    message=e.message,
                )
                for e in page
            ],
        )


async def watch_progress(config: Settings, actor: Actor, query: WatchTask) -> TaskProgress:
    """Long polling keeps no transaction/connection open while awaiting new commits."""
    db = Database(config.database.url.get_secret_value())
    deadline = time.monotonic() + query.wait_seconds
    try:
        while True:
            result = await _snapshot(db, actor, query)
            if (
                result.events
                or result.state not in {"queued", "running"}
                or time.monotonic() >= deadline
            ):
                return result
            await asyncio.sleep(min(0.5, max(0, deadline - time.monotonic())))
    finally:
        await db.close()
