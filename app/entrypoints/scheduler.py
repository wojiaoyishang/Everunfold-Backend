"""One Scheduler owns periodic dispatch and its resident child processes."""

import asyncio
import contextlib
import multiprocessing
import os
import signal
import uuid
from datetime import timedelta
from typing import Any

from redis import Redis
from sqlalchemy import select

from app.core.database import Database
from app.core.jobs.definitions import definitions
from app.core.jobs.execution import resident_entry
from app.core.jobs.models import TaskRun
from app.core.jobs.service import now, reconcile
from app.core.jobs.telemetry import Reporter
from app.core.task_queue import celery_app, settings


async def run() -> None:
    token = uuid.uuid4().hex
    key = settings.redis_prefix + "scheduler:settings"
    client = Redis.from_url(
        settings.redis.url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
    )
    db = Database(settings.database.url.get_secret_value())
    reporter = Reporter(settings, "scheduler")
    children: dict[str, tuple[Any, Any]] = {}
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    acquired = False
    try:
        acquired = bool(await asyncio.to_thread(client.set, key, token, nx=True, px=30000))
        if not acquired:
            raise RuntimeError("A scheduler already owns this namespace")
        while not stopping.is_set():
            renewed = await asyncio.to_thread(
                client.eval,
                "if redis.call('get',KEYS[1]) == ARGV[1] then "
                "return redis.call('pexpire',KEYS[1],30000) else return 0 end",
                1,
                key,
                token,
            )
            if not renewed:
                raise RuntimeError("Scheduler lease lost")
            try:
                async with db.session_factory() as session:
                    await reconcile(session)
                    pending = list(
                        await session.scalars(
                            select(TaskRun)
                            .where(TaskRun.status == "queued")
                            .order_by(TaskRun.planned_at)
                            .limit(50)
                        )
                    )
                for identity, (child, stop) in list(children.items()):
                    async with db.session_factory() as session:
                        row = await session.get(TaskRun, uuid.UUID(identity))
                        expired = (
                            not row
                            or row.status not in {"queued", "running"}
                            or (
                                row.status == "running"
                                and row.lease_until
                                and row.lease_until < now()
                            )
                        )
                    if expired:
                        stop.set()
                    if not child.is_alive():
                        child.join()
                        child.close()
                        del children[identity]
                    elif expired:
                        child.terminate()
                broker_failed = False
                for row in pending:
                    spec = definitions().get(row.task_id)
                    if not spec:
                        continue
                    if spec[1].kind == "resident":
                        if str(row.id) not in children:
                            ctx = multiprocessing.get_context("spawn")
                            event = ctx.Event()
                            child = ctx.Process(
                                target=resident_entry,
                                args=(str(row.id), os.getpid(), event),
                                name="scheduler:" + row.task_id,
                            )
                            child.start()
                            children[str(row.id)] = child, event
                    elif not row.sent_at or row.sent_at < now() - timedelta(seconds=30):
                        try:
                            await asyncio.to_thread(
                                celery_app.send_task,
                                "everunfold.runtime.execute",
                                args=[str(row.id)],
                                expires=300,
                                retry=False,
                            )
                        except Exception:
                            broker_failed = True
                            break
                        async with db.session_factory() as session:
                            current = await session.get(TaskRun, row.id)
                            if current:
                                current.sent_at = now()
                                await session.commit()
                reporter.pulse(
                    state="degraded" if broker_failed else "online",
                    error="broker_unavailable" if broker_failed else None,
                    last_scan=now().isoformat(),
                    residents=len(children),
                    pending=len(pending),
                )
            except Exception as exc:
                reporter.pulse(state="degraded", error=type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=1)
    finally:
        for _, stop in children.values():
            stop.set()
        for child, _ in children.values():
            await asyncio.to_thread(child.join, 8)
            if child.is_alive():
                child.terminate()
                await asyncio.to_thread(child.join, 2)
            if child.is_alive():
                child.kill()
                await asyncio.to_thread(child.join, 2)
            child.close()
        reporter.close()
        try:
            if acquired:
                client.eval(
                    "if redis.call('get',KEYS[1]) == ARGV[1] then "
                    "return redis.call('del',KEYS[1]) else return 0 end",
                    1,
                    key,
                    token,
                )
        finally:
            client.close()
            await db.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
