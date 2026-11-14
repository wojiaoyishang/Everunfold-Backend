"""Verify per-request counting and shared aggregates with real services."""

import asyncio
import os
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings, load_settings
from app.core.database import Database
from app.core.query_cache_metrics import BUCKET_SECONDS, QueryCacheMetrics, count_query_sql
from app.entrypoints.api import create_app

_OBSERVE_IN_SECOND_PROCESS = """
import asyncio
import sys
from redis.asyncio import Redis
from app.core.config import load_settings
from app.core.query_cache_metrics import QueryCacheMetrics

async def main():
    settings = load_settings(sys.argv[1], environ={})
    async with Redis.from_url(settings.redis.url.get_secret_value()) as redis:
        metrics = QueryCacheMetrics(redis, sys.argv[2])
        metrics.record("site", "hit", 4, 1)
        metrics.record("content_list", "bypass", 35, 6)
        await metrics.close()

asyncio.run(main())
"""


def test_public_query_metrics_need_settings_credentials(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/v1/admin/frontend-nodes/query-cache?hours=1")
        assert response.status_code == 401
        assert "no-store" in response.headers["Cache-Control"]


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Requires isolated PostgreSQL/Redis"
)
def test_metrics_aggregate_counts_across_processes_during_cache_outage() -> None:
    path = os.environ.get("TEST_CONFIG_FILE", "config.test.local.toml")
    settings = load_settings(path, environ={})
    database_url = settings.database.url.get_secret_value()
    if make_url(
        database_url
    ).database != "everunfold_test" or not settings.redis.namespace_scope.startswith("test"):
        pytest.fail("Metrics integration refuses non-test database or namespace")
    prefix = f"{settings.redis_prefix}query-metrics-test:{uuid4().hex}:"

    async def verify() -> None:
        database = Database(database_url)
        async with Redis.from_url(settings.redis.url.get_secret_value()) as redis:
            metrics = QueryCacheMetrics(redis, prefix)
            try:
                async with database.session_factory() as session:
                    await session.execute(text("SELECT 1"))
                    with count_query_sql() as outer:
                        await session.execute(text("SELECT 1"))
                        with count_query_sql() as nested:
                            await session.execute(text("SELECT 1"))
                        await session.execute(text("SELECT 1"))
                    assert nested.count == 1

                metrics.record("site", "miss", 7, outer.count)
                metrics.record("taxonomies", "error", 19, 2)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", _OBSERVE_IN_SECOND_PROCESS, path, prefix
                )
                assert await asyncio.wait_for(process.wait(), 10) == 0
                await metrics.close()
                report = await QueryCacheMetrics(redis, prefix).report(1, "unavailable")

                assert report.cache_status == "unavailable"
                assert report.metrics_status == "ready"
                assert len(report.trend) == 60
                assert report.total.queries == sum(point.queries for point in report.trend) == 4
                assert (report.total.hits, report.total.misses) == (1, 1)
                assert (report.total.errors, report.total.bypasses) == (1, 1)
                assert report.total.sql_queries == 11
                assert report.total.mean_ms == 16.25
                assert report.total.p95_ms == 35
                assert report.total.hit_rate == 0.5
                site, taxonomies, contents = report.businesses
                assert (site.queries, taxonomies.queries, contents.queries) == (2, 1, 1)
                assert taxonomies.hit_rate is None

                for hours, seconds in BUCKET_SECONDS.items():
                    namespace = f"{prefix}runtime:query-metrics:v1:{hours}:"
                    keys = [key async for key in redis.scan_iter(match=namespace + "*")]
                    assert 1 <= len(keys) <= 2
                    for key in keys:
                        assert 0 < await redis.ttl(key) <= hours * 3600 + 60
                        assert int(key.rsplit(b":", 1)[-1]) % seconds == 0
            finally:
                await metrics.close()
                for key in [key async for key in redis.scan_iter(match=prefix + "*")]:
                    await redis.delete(key)
                await database.close()

    asyncio.run(verify())
