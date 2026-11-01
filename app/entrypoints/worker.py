"""Celery owns delivery work; importing it never initializes FastAPI modules."""

import asyncio

from app.core.task_queue import celery_app, settings
from app.modules.settings.delivery import deliver_batch


@celery_app.task(name="everunfold.settings.deliver")  # type: ignore[untyped-decorator]
def deliver_settings() -> int:
    return asyncio.run(deliver_batch(settings))
