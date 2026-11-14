"""Bounded, cross-process query metrics, independent of the result-cache instance."""

import asyncio
import time
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import Context, ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.query_cache_schemas import (
    QueryBusiness,
    QueryCacheBusinessStats,
    QueryCachePoint,
    QueryCacheReport,
    QueryCacheStats,
    QueryCacheStatus,
    QueryMetricsStatus,
    QueryOutcome,
    QueryReportHours,
)

BUSINESSES: tuple[QueryBusiness, ...] = ("site", "taxonomies", "content_list")
BUCKET_SECONDS: dict[QueryReportHours, int] = {1: 60, 6: 300, 24: 900}
LATENCY_LIMITS_MS = (
    1,
    2,
    5,
    10,
    20,
    35,
    50,
    75,
    100,
    150,
    250,
    500,
    1000,
    2000,
    4000,
    8000,
    16000,
    32000,
    64000,
    128000,
)
OUTCOME_FIELDS: dict[QueryOutcome, str] = {
    "hit": "hits",
    "miss": "misses",
    "bypass": "bypasses",
    "error": "errors",
}
FLUSH_SECONDS = 5
REDIS_TIMEOUT_SECONDS = 0.75


@dataclass(slots=True)
class QuerySqlCount:
    count: int = 0


_sql_count: ContextVar[QuerySqlCount | None] = ContextVar("query_cache_sql_count", default=None)


@contextmanager
def count_query_sql() -> Iterator[QuerySqlCount]:
    counter = QuerySqlCount()
    token = _sql_count.set(counter)
    try:
        yield counter
    finally:
        _sql_count.reset(token)


def count_cursor_execute(*_: object) -> None:
    if (counter := _sql_count.get()) is not None:
        counter.count += 1


def query_stats(values: Counter[str]) -> QueryCacheStats:
    hits, misses = values["hits"], values["misses"]
    bypasses, errors = values["bypasses"], values["errors"]
    queries = hits + misses + bypasses + errors
    p95: int | None = None
    if queries:
        remaining = ceil(queries * 0.95)
        for index, limit in enumerate(LATENCY_LIMITS_MS):
            remaining -= values[f"latency_{index}"]
            if remaining <= 0:
                p95 = limit
                break
    return QueryCacheStats(
        queries=queries,
        hits=hits,
        misses=misses,
        bypasses=bypasses,
        errors=errors,
        sql_queries=values["sql"],
        mean_ms=round(values["duration_us"] / queries / 1000, 2) if queries else None,
        p95_ms=p95,
        hit_rate=hits / (hits + misses) if hits + misses else None,
    )


class QueryCacheMetrics:
    def __init__(self, redis: Redis, prefix: str) -> None:
        self.redis = redis
        self.prefix = prefix + "runtime:query-metrics:v1:"
        self.pending: dict[tuple[QueryReportHours, int], Counter[str]] = {}
        self.stop = asyncio.Event()
        self.flush_task: asyncio.Task[None] | None = None
        self.last_flush_failed = False

    def record(
        self, business: QueryBusiness, outcome: QueryOutcome, duration_ms: float, sql_count: int
    ) -> None:
        now = int(time.time())
        latency_bucket = bisect_left(LATENCY_LIMITS_MS, duration_ms)
        values = {
            f"{business}:{OUTCOME_FIELDS[outcome]}": 1,
            f"{business}:sql": sql_count,
            f"{business}:duration_us": round(duration_ms * 1000),
            f"{business}:latency_{latency_bucket}": 1,
        }
        for hours, step in BUCKET_SECONDS.items():
            bucket = now - now % step
            self.pending.setdefault((hours, bucket), Counter()).update(values)
        if self.flush_task is None:
            self.flush_task = asyncio.create_task(self._flush_periodically(), context=Context())

    def _key(self, hours: QueryReportHours, bucket: int) -> str:
        return f"{self.prefix}{hours}:{bucket}"

    async def _flush_periodically(self) -> None:
        try:
            while self.pending:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stop.wait(), timeout=FLUSH_SECONDS)
                await self._flush()
        finally:
            self.flush_task = None

    async def _flush(self) -> None:
        buffered, self.pending = self.pending, {}
        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS), self.redis.pipeline() as pipe:
                for (hours, bucket), fields in buffered.items():
                    key = self._key(hours, bucket)
                    for field, value in fields.items():
                        pipe.hincrby(key, field, value)
                    pipe.expireat(key, bucket + hours * 3600 + 60)
                await pipe.execute()
        except (RedisError, TimeoutError):
            # A timed-out write may already have committed. Replaying it would overcount.
            self.last_flush_failed = True
        else:
            self.last_flush_failed = False

    async def close(self) -> None:
        task = self.flush_task
        if task is not None:
            self.stop.set()
            await task

    async def report(
        self, hours: QueryReportHours, cache_status: QueryCacheStatus
    ) -> QueryCacheReport:
        now = datetime.now(UTC)
        found: dict[int, dict[QueryBusiness, Counter[str]]] = {}
        step = BUCKET_SECONDS[hours]
        end = int(now.timestamp()) // step * step + step
        buckets = range(end - hours * 3600, end, step)
        metrics_status: QueryMetricsStatus = "unavailable" if self.last_flush_failed else "ready"
        try:
            async with (
                asyncio.timeout(REDIS_TIMEOUT_SECONDS),
                self.redis.pipeline(transaction=False) as pipe,
            ):
                for bucket in buckets:
                    pipe.hgetall(self._key(hours, bucket))
                responses = await pipe.execute()
            for bucket, response in zip(buckets, responses, strict=True):
                row: dict[QueryBusiness, Counter[str]] = {name: Counter() for name in BUSINESSES}
                for key, value in response.items():
                    identifier = key.decode() if isinstance(key, bytes) else key
                    business, _, field = identifier.partition(":")
                    for name in BUSINESSES:
                        if business == name:
                            row[name][field] = int(value)
                            break
                found[bucket] = row
        except (RedisError, TimeoutError, ValueError):
            metrics_status = "unavailable"

        total = Counter[str]()
        totals: dict[QueryBusiness, Counter[str]] = {name: Counter() for name in BUSINESSES}
        trend: list[QueryCachePoint] = []
        business_trends: dict[QueryBusiness, list[QueryCachePoint]] = {
            name: [] for name in BUSINESSES
        }
        for bucket in buckets:
            at = datetime.fromtimestamp(bucket, UTC)
            counts = Counter[str]()
            row = found.get(bucket, {})
            for name in BUSINESSES:
                values = row.get(name, Counter())
                totals[name].update(values)
                counts.update(values)
                business_trends[name].append(
                    QueryCachePoint(at=at, **query_stats(values).model_dump())
                )
            total.update(counts)
            trend.append(QueryCachePoint(at=at, **query_stats(counts).model_dump()))
        return QueryCacheReport(
            cache_status=cache_status,
            metrics_status=metrics_status,
            hours=hours,
            bucket_seconds=step,
            checked_at=now,
            total=query_stats(total),
            businesses=[
                QueryCacheBusinessStats(
                    business=name,
                    trend=business_trends[name],
                    **query_stats(totals[name]).model_dump(),
                )
                for name in BUSINESSES
            ],
            trend=trend,
        )
