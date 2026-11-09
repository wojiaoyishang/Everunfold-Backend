"""Celery owns delivery work; importing it never initializes FastAPI modules."""

import asyncio

from app.core.task_queue import celery_app, settings
from app.modules.auth.callback_delivery import deliver_callbacks
from app.modules.content.scheduled_publication import publish_due
from app.modules.settings.delivery import deliver_batch


@celery_app.task(name="everunfold.settings.deliver")  # type: ignore[untyped-decorator]
def deliver_settings() -> int:
    return asyncio.run(deliver_batch(settings))


@celery_app.task(name="everunfold.auth.deliver_callbacks")  # type: ignore[untyped-decorator]
def deliver_auth_callbacks() -> int:
    return asyncio.run(deliver_callbacks(settings))


@celery_app.task(name="everunfold.content.publish_due")  # type: ignore[untyped-decorator]
def publish_scheduled_content() -> int:
    return asyncio.run(publish_due(settings))


@celery_app.task(name="everunfold.runtime.execute", soft_time_limit=3610, time_limit=3620)  # type: ignore[untyped-decorator]
def execute_registered_task(identity: str) -> None:
    from app.core.jobs.execution import execute

    asyncio.run(execute(identity))


from celery.signals import worker_ready, worker_shutdown  # type: ignore[import-untyped] # noqa: E402

from app.core.jobs.telemetry import Reporter  # noqa: E402

_reporter: Reporter | None = None


@worker_ready.connect(weak=False)  # type: ignore[untyped-decorator]
def started(**kwargs: object) -> None:
    global _reporter
    _reporter = Reporter(settings, "worker")
    _reporter.data["queue"] = settings.redis_prefix + "settings"
    _reporter.background()


@worker_shutdown.connect(weak=False)  # type: ignore[untyped-decorator]
def stopped(**kwargs: object) -> None:
    if _reporter:
        _reporter.close()
