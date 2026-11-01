"""Celery owns delivery work; importing it never initializes FastAPI modules."""

import asyncio
from typing import Any

from celery import Celery  # type: ignore[import-untyped]

from app.core.config import load_settings
from app.modules.settings.delivery import deliver_batch

settings = load_settings()
celery_app: Any = Celery("everunfold", broker=settings.celery.broker_url.get_secret_value())
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue=settings.redis_prefix + "settings",
    broker_transport_options={"global_keyprefix": settings.redis_prefix + "celery:"},
    broker_connection_retry_on_startup=True,
)


@celery_app.task(name="everunfold.settings.deliver")  # type: ignore[untyped-decorator]
def deliver_settings() -> int:
    return asyncio.run(deliver_batch(settings))
