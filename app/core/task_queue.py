"""Queue configuration shared by producers and workers; no business task imports."""

from typing import Any

from celery import Celery  # type: ignore[import-untyped]

from app.core.config import load_settings
from app.core.monitor_config import monitoring_config

settings = load_settings()
monitor = monitoring_config()
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
    broker_transport_options={
        "global_keyprefix": settings.redis_prefix + "celery:",
        "socket_timeout": 2,
        "socket_connect_timeout": 2,
    },
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    task_publish_retry=False,
    worker_send_task_events=bool(monitor and monitor.celery_exporter),
)
