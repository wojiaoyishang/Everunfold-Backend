"""Single public-to-the-monitoring-network scrape, with cached private providers."""

import asyncio
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx2 as httpx
from fastapi import FastAPI, Request, Response
from prometheus_client import CollectorRegistry, generate_latest, multiprocess
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.parser import text_string_to_metric_families
from prometheus_fastapi_instrumentator import Instrumentator
from redis.asyncio import Redis

from app.core.jobs.service import available
from app.core.monitor_config import MonitoringConfig
from app.core.plugins.definition import Plugin, PluginRuntime
from app.core.plugins.loader import PluginCatalog
from app.core.responses import JSONResponseFail
from app.core.runtime import Runtime
from app.modules.settings.node_registry import read_directory
from app.modules.settings.nodes_schemas import NodeStatus, ReceiverProbe
from app.modules.settings.sync_status import read_history

logger = logging.getLogger(__name__)
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
CACHE_TTL = 105


@dataclass(frozen=True)
class PluginMetric:
    name: str
    description: str
    value: float
    kind: Literal["gauge", "counter"] = "gauge"
    labels: tuple[tuple[str, str], ...] = ()


@dataclass(eq=False)
class Families:
    series: list[Metric]

    def collect(self) -> Iterable[Metric]:
        return self.series


def gauge(name: str, description: str, value: float) -> Metric:
    return GaugeMetricFamily("everunfold_" + name, description, value=value)


def expose(families: Iterable[Metric]) -> bytes:
    ordered = list(families)
    if len({family.name for family in ordered}) != len(ordered):
        raise ValueError("Duplicate metric family")
    registry = CollectorRegistry()
    registry.register(Families(ordered))
    return generate_latest(registry)


def plugin_family(owner: str, value: PluginMetric) -> Metric:
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value.name)
        or value.kind not in {"gauge", "counter"}
        or len(value.labels) > 5
    ):
        raise ValueError("Invalid plugin metric")
    labels = dict(value.labels)
    if len(labels) != len(value.labels) or any(
        not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", key) or len(label) > 80
        for key, label in labels.items()
    ):
        raise ValueError("Invalid plugin metric labels")
    namespace = re.sub(r"[^a-zA-Z0-9]", "_", owner)
    family = CounterMetricFamily if value.kind == "counter" else GaugeMetricFamily
    metric = family(
        f"everunfold_plugin_{namespace}_{value.name}", value.description, labels=list(labels)
    )
    metric.add_metric(list(labels.values()), value.value)
    return metric


async def scheduler_metrics(runtime: Runtime) -> list[Metric]:
    # The lease identifies the active Scheduler. Ignore stopped and expired snapshots.
    reports = []
    workers = 0
    async with asyncio.timeout(2):
        async for key in runtime.redis.scan_iter(
            match=runtime.settings.redis_prefix + "runtime:instance:*", count=100
        ):
            data = await runtime.redis.get(key)
            if data:
                record = json.loads(data)
                if time.time() - record["heartbeat"] <= 20:
                    if record["role"] == "scheduler" and record["state"] != "stopped":
                        reports.append(record)
                    elif record["role"] == "worker" and record["state"] == "online":
                        workers += 1
    current = max(reports, key=lambda row: row["heartbeat"], default=None)
    active = bool(current and time.time() - current["heartbeat"] <= 20)
    metrics = [
        gauge("celery_workers_online", "Celery worker instances with a fresh heartbeat.", workers),
        gauge("scheduler_up", "Scheduler has a fresh heartbeat.", int(active)),
        gauge(
            "scheduler_healthy",
            "Scheduler has an online loop.",
            int(current is not None and active and current["state"] == "online"),
        ),
    ]
    if not (active and current):
        return metrics
    data = await runtime.redis.get(
        runtime.settings.redis_prefix + "runtime:metrics:" + current["id"]
    )
    if data:
        current.update(json.loads(data))
    for field, name, description in (
        ("heartbeat", "heartbeat_timestamp_seconds", "Latest Scheduler heartbeat."),
        (
            "last_successful_scan",
            "last_successful_scan_timestamp_seconds",
            "Latest successful Scheduler iteration.",
        ),
        ("oldest_due_at", "oldest_due_timestamp_seconds", "Oldest due unsubmitted task."),
        ("residents", "resident_tasks", "Scheduler resident subprocesses."),
    ):
        if isinstance(current.get(field), int | float):
            metrics.append(gauge("scheduler_" + name, description, current[field]))
    dispatch = CounterMetricFamily(
        "everunfold_scheduler_dispatch", "Scheduler submissions.", labels=["result"]
    )
    for outcome in ("success", "failure"):
        dispatch.add_metric([outcome], current.get("dispatch_" + outcome, 0))
    return [*metrics, dispatch]


async def queue_metrics(runtime: Runtime) -> list[Metric]:
    config = runtime.settings
    key = config.redis_prefix + "celery:" + config.redis_prefix + "settings"
    async with (
        Redis.from_url(
            config.celery.broker_url.get_secret_value(), socket_timeout=2, socket_connect_timeout=2
        ) as broker,
        broker.pipeline(transaction=False) as batch,
    ):
        for suffix in ("", "\x06\x163", "\x06\x166", "\x06\x169"):
            batch.llen(key + suffix)
        sizes = await batch.execute()
    return [
        gauge(
            "celery_broker_queued_messages",
            "Broker queue depth including all Redis priority lists.",
            sum(sizes),
        )
    ]


async def sync_metrics(runtime: Runtime) -> list[Metric]:
    async with runtime.database.session_factory() as session:
        directory = await read_directory(session, runtime.settings)
        receivers = directory.receivers
        nodes = [
            NodeStatus(
                id=node_id,
                enabled=True,
                base_url=entry.base_url,
                receiver_configured=node_id in receivers,
                receiver=ReceiverProbe(state="unavailable"),
            )
            for node_id, entry in directory.entries.items()
            if entry.enabled
        ]
        await read_history(session, nodes, datetime.now(UTC), runtime.settings.outbox.max_attempts)
    failed = GaugeMetricFamily(
        "everunfold_sync_failed",
        "Latest version exhausted delivery retries.",
        labels=["node", "resource"],
    )
    gap = GaugeMetricFamily(
        "everunfold_sync_revision_gap",
        "Expected revision minus confirmed revision.",
        labels=["node", "resource"],
    )
    for node in nodes:
        for resource, delivery in node.sync.items():
            if not node.receiver_configured:
                continue
            labels = [node.id, resource]
            failed.add_metric(labels, int(delivery.state == "failed"))
            if delivery.acknowledged is not None:
                gap.add_metric(labels, max(0, delivery.expected - delivery.acknowledged))
            else:
                gap.add_metric(labels, delivery.expected)
    return [failed, gap]


async def scrape_source(url: str, prefix: str, token: str = "") -> list[Metric]:
    headers = {"Accept": CONTENT_TYPE}
    if token:
        headers["Authorization"] = "Bearer " + token
    async with (
        httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=6) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise ValueError("Metrics response exceeded limit")
    return [
        family
        for family in text_string_to_metric_families(data.decode())
        if family.name.startswith(prefix) and family.name != "celery_queue_length"
    ]


class Monitoring:
    def __init__(self, config: MonitoringConfig, plugins: PluginCatalog) -> None:
        self.config, self.plugins = config, plugins
        self.registry = CollectorRegistry()
        self.key = ""
        self.names: tuple[str, ...] = ()

    async def refresh(self, runtime: Runtime) -> None:
        self.key = runtime.settings.redis_prefix + "monitoring:snapshot:v1"
        config = self.config
        sources: dict[str, Callable[[], Awaitable[list[Metric]]]] = {
            "scheduler": lambda: scheduler_metrics(runtime),
            "broker": lambda: queue_metrics(runtime),
            "sync": lambda: sync_metrics(runtime),
        }
        if config.frontend:
            sources["nodejs"] = lambda: scrape_source(
                config.frontend + "/internal/observability/v1/metrics",
                "everunfold_node_",
                config.node_token,
            )
        if config.celery_exporter:
            sources["celery"] = lambda: scrape_source(
                config.celery_exporter + "/metrics", "celery_"
            )
        self.names = tuple(sources)
        providers = [
            p for p in self.plugins.plugins if type(p.definition).metrics is not Plugin.metrics
        ]

        async def collect(name: str, read: Callable[[], Awaitable[list[Metric]]]) -> list[Metric]:
            try:
                async with asyncio.timeout(8):
                    samples = await read()
                expose(samples)
                return [*samples, source_status(name, True)]
            except Exception as error:
                logger.debug("Metrics source %s unavailable (%s)", name, type(error).__name__)
                return [source_status(name, False)]

        async def plugins() -> list[Metric]:
            if not providers:
                return []
            async with runtime.database.session_factory() as session:
                enabled = await available(session)
            values = []
            names: set[str] = set()
            status = GaugeMetricFamily(
                "everunfold_plugin_metrics_up",
                "Latest plugin collection succeeded.",
                labels=["plugin"],
            )
            for item in providers:
                if item.info.id not in enabled:
                    continue
                try:
                    async with asyncio.timeout(1):
                        entries = await item.definition.metrics(
                            PluginRuntime(role="api", runtime=runtime)
                        )
                        if len(entries) > 50:
                            raise ValueError("Too many plugin metrics")
                        result = [plugin_family(item.info.id, entry) for entry in entries]
                        expose(result)  # Validate family names and duplicates before publishing.
                    if names.intersection(metric.name for metric in result):
                        raise ValueError("Metric namespace collision")
                    names.update(metric.name for metric in result)
                    values.extend(result)
                    status.add_metric([item.info.id], 1)
                except Exception as error:
                    status.add_metric([item.info.id], 0)
                    logger.warning(
                        "Metrics plugin %s unavailable (%s)", item.info.id, type(error).__name__
                    )
            return [*values, status]

        while True:
            try:
                if await runtime.redis.set(self.key + ":lease", "1", nx=True, ex=30):
                    parts = await asyncio.gather(
                        *(collect(name, read) for name, read in sources.items()),
                        collect("plugins", plugins),
                    )
                    payload = expose(
                        metric
                        for part in parts
                        for metric in part
                        if metric.name != "everunfold_metrics_source_up"
                    )
                    status = GaugeMetricFamily(
                        "everunfold_metrics_source_up",
                        "Last collection succeeded.",
                        labels=["source"],
                    )
                    for part in parts:
                        for metric in part:
                            if metric.name == status.name:
                                status.samples.extend(metric.samples)
                    await runtime.redis.set(
                        self.key,
                        payload
                        + expose(
                            [
                                status,
                                gauge(
                                    "metrics_snapshot_timestamp_seconds",
                                    "Most recent collection timestamp.",
                                    time.time(),
                                ),
                            ]
                        ),
                        ex=CACHE_TTL,
                    )
            except Exception as error:
                logger.debug("Metrics snapshot unavailable (%s)", type(error).__name__)
            await asyncio.sleep(10)

    def install(self, application: FastAPI) -> None:
        Instrumentator(
            registry=self.registry,
            excluded_handlers=["/metrics", "/health/.*"],
            should_group_untemplated=True,
        ).instrument(application, metric_namespace="everunfold_api")

        @application.get("/metrics", include_in_schema=False)
        async def metrics(request: Request) -> Response:
            peer = request.client.host if request.client else ""
            presented = request.headers.get("Authorization", "").encode()
            if not self.config.allowed(peer) or not hmac.compare_digest(
                presented, ("Bearer " + self.config.token).encode()
            ):
                return JSONResponseFail(status_code=403, msg="Forbidden")
            registry = self.registry
            if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
                registry = CollectorRegistry()
                multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
            cached = None
            runtime = request.app.state.runtime
            if runtime is not None and self.key:
                try:
                    async with asyncio.timeout(0.5):
                        cached = await runtime.redis.get(self.key)
                except Exception:
                    pass
            if cached is None:
                unavailable = GaugeMetricFamily(
                    "everunfold_metrics_source_up", "Last collection succeeded.", labels=["source"]
                )
                for name in self.names:
                    unavailable.add_metric([name], 0)
                cached = expose([unavailable])
            return Response(
                generate_latest(registry) + cached,
                headers={
                    "Content-Type": CONTENT_TYPE,
                    "Cache-Control": "no-store",
                },
            )


def source_status(name: str, available: bool) -> Metric:
    metric = GaugeMetricFamily(
        "everunfold_metrics_source_up", "Last collection succeeded.", labels=["source"]
    )
    metric.add_metric([name], int(available))
    return metric
