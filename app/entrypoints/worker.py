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
