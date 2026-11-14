"""Two FastAPI processes exercise an isolated cache and a throwaway PostgreSQL database."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
import tomllib
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2
import pytest
from redis import Redis, RedisError
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.modules.content.models import Content, SiteSettings
from app.modules.content.publication import ContentPublication
from app.modules.settings.cache_event_schema import CacheChange
from tests.test_auth_http_processes import ROOT, free_port, uvicorn_process
from tests.test_settings_processes import background, toml_text, wait_for


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Explicit isolated database test required"
)
def test_public_query_cache_is_shared_and_observes_postgresql_revisions(tmp_path: Path) -> None:
    binary = os.environ.get("EVERUNFOLD_TEST_REDIS_SERVER") or shutil.which("redis-server")
    if not binary:
        pytest.skip("Set EVERUNFOLD_TEST_REDIS_SERVER to an installed redis-server")
    original_path = ROOT / "config.test.local.toml"
    config = load_settings(original_path, environ={})
    original_url = make_url(config.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    assert config.redis.namespace_scope.startswith("test")
    database_name = "everunfold_query_test_" + uuid4().hex[:12]
    database_url = original_url.set(database=database_name).render_as_string(hide_password=False)
    redis_port = free_port()
    config_file = tmp_path / "cache-test.toml"
    raw = tomllib.loads(original_path.read_text(encoding="utf-8"))
    raw["database"]["url"] = database_url
    raw["redis"]["namespace_scope"] = "test-query-" + uuid4().hex[:12]
    raw["frontends"] = {}
    raw.pop("frontend_nodes_file", None)
    raw["query_cache"] = {"url": f"redis://127.0.0.1:{redis_port}/0", "timeout_ms": 150}
    raw["media"] = {"uploads_path": str(tmp_path / "uploads")}
    config_file.write_text(toml_text(raw), encoding="utf-8")
    config_file.chmod(0o600)
    env = {
        name: value
        for name, value in os.environ.items()
        if name
        not in (
            "BACKEND_BASE_URL",
            "DATABASE_URL",
            "REDIS_URL",
            "QUERY_CACHE_REDIS_URL",
            "EVERUNFOLD_QUERY_CACHE_DEFAULT_URL",
            "EVERUNFOLD_ADMIN_USERNAME",
            "EVERUNFOLD_ADMIN_PASSWORD",
        )
    }
    env["CONFIG_FILE"] = str(config_file)

    async def add_article(path: str, *, opens_at: datetime | None = None) -> UUID:
        database = Database(database_url)
        try:
            async with database.session_factory() as session, session.begin():
                now = datetime.now(UTC)
                article = Content(
                    source="test",
                    kind="article",
                    title=path,
                    path=path,
                    excerpt="cache visibility proof",
                    author="cache test",
                    published_at=now,
                    updated_at=now,
                    visibility="password" if opens_at else "public",
                    access_password_hash="scheduled proof hash" if opens_at else None,
                    opens_at=opens_at,
                )
                session.add(article)
                await ContentPublication(session, load_settings(config_file, environ={})).stage()
                return article.id
        finally:
            await database.close()

    async def withdraw(article: UUID) -> None:
        database = Database(database_url)
        try:
            async with database.session_factory() as session, session.begin():
                item = await session.get_one(Content, article)
                item.publication_status = "withdrawn"
                await ContentPublication(session, load_settings(config_file, environ={})).stage(
                    CacheChange("content_withdrawn", article, (item.path,))
                )
        finally:
            await database.close()

    async def revision() -> int:
        database = Database(database_url)
        try:
            async with database.session_factory() as session:
                return (await session.scalars(select(SiteSettings.content_generation))).one()
        finally:
            await database.close()

    async def cleanup() -> None:
        connection = await asyncpg.connect(
            original_url.set(drivername="postgresql", database="postgres").render_as_string(
                hide_password=False
            )
        )
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
        finally:
            await connection.close()

    try:
        initialized = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=40,
            check=False,
        )
        assert initialized.returncode == 0, "Isolated database initialization failed"
        article_id = asyncio.run(add_article("/cache-test"))
        # Allocate two different listeners, excluding the dedicated Redis port.
        ports: set[int] = set()
        while len(ports) < 2:
            candidate = free_port()
            if candidate != redis_port:
                ports.add(candidate)
        port_a, port_b = ports
        with ExitStack() as stack:
            redis_process = stack.enter_context(
                background(
                    [
                        binary,
                        "--bind",
                        "127.0.0.1",
                        "--port",
                        str(redis_port),
                        "--save",
                        "",
                        "--appendonly",
                        "no",
                    ],
                    env,
                    tmp_path,
                )
            )
            cache = stack.enter_context(Redis(host="127.0.0.1", port=redis_port, socket_timeout=1))

            def ready() -> bool:
                try:
                    return bool(cache.ping())
                except RedisError:
                    return False

            wait_for(ready, timeout=5)
            for port in (port_a, port_b):
                stack.enter_context(uvicorn_process(port, env))
            first, second = (
                stack.enter_context(
                    httpx2.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=4)
                )
                for port in (port_a, port_b)
            )

            def read(client: httpx2.Client, path: str) -> dict[str, object]:
                response = client.get(path)
                response.raise_for_status()
                result: dict[str, object] = response.json()["data"]
                return result

            for path in ("site", "taxonomies", "contents"):
                endpoint = "/v1/" + path
                cold = read(first, endpoint)
                assert read(second, endpoint) == cold
            stats = cache.info("stats")
            assert stats["keyspace_hits"] == 3
            assert stats["keyspace_misses"] == 3
            assert cache.dbsize() == 3
            assert first.get("/v1/contents?q=cache-test").status_code == 200
            assert first.get("/v1/contents?page=4").status_code == 200
            assert cache.dbsize() == 3

            asyncio.run(withdraw(article_id))
            page = read(second, "/v1/contents")
            assert page["items"] == []
            assert cache.info("stats")["keyspace_misses"] == 4

            opens = datetime.now(UTC) + timedelta(seconds=5)
            asyncio.run(add_article("/cache-scheduled", opens_at=opens))
            before_open = asyncio.run(revision())
            early = first.get("/v1/contents").json()["data"]["items"][0]
            assert early["access"] == "scheduled" and early["excerpt"] == ""
            assert second.get("/v1/contents").json()["data"]["items"][0] == early
            [key] = cache.keys(f"*:query-cache-v1:*:{before_open}:content_list:*")
            assert 0 < cache.pttl(key) <= (opens - datetime.now(UTC)).total_seconds() * 1000
            seconds_to_open = max(0.0, (opens - datetime.now(UTC)).total_seconds())
            # A Redis deadline or clock mismatch must not revive a pre-opening result.
            assert cache.expire(key, 45)
            time.sleep(seconds_to_open + 0.1)
            later = second.get("/v1/contents").json()["data"]["items"][0]
            assert later["access"] == "public" and later["excerpt"] == "cache visibility proof"
            assert asyncio.run(revision()) == before_open

            redis_process.terminate()
            redis_process.wait(timeout=5)
            assert first.get("/health/ready").status_code == 200
            assert first.get("/v1/contents").json()["data"]["items"][0] == later
            assert second.get("/v1/contents").json()["data"]["items"][0] == later
    finally:
        asyncio.run(cleanup())
