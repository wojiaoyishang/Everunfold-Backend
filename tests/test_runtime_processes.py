"""Real scheduler children, competing instances, durable claims and checkpoint recovery."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from redis import Redis
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from tests.test_settings_processes import background, toml_text, wait_for

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1", reason="Requires explicit isolated database"
)
def test_scheduler_owns_residents_and_recovers(tmp_path: Path) -> None:
    config = load_settings(ROOT / "config.test.local.toml")
    base = make_url(config.database.url.get_secret_value())
    assert base.database == "everunfold_test"
    name = "everunfold_runtime_test_" + uuid4().hex[:10]
    url = base.set(database=name)
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text())
    raw["database"]["url"] = url.render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "runtime-test-" + uuid4().hex
    raw["frontends"] = {}
    path = tmp_path / "config.toml"
    path.write_text(toml_text(raw))
    env = {**os.environ, "CONFIG_FILE": str(path)}
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "EVERUNFOLD_ADMIN_PASSWORD",
        "EVERUNFOLD_ADMIN_USERNAME",
    ):
        env.pop(key, None)

    async def sql(query: str):
        conn = await asyncpg.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            return await conn.fetch(query)
        finally:
            await conn.close()

    def rows(query: str):
        return asyncio.run(sql(query))

    command = [sys.executable, "-m", "app.entrypoints.scheduler"]
    try:
        subprocess.run(
            [sys.executable, "init.database.py", "--config", str(path)],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.register_backend_plugins",
                "--config",
                str(path),
                "--apply",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
        with ExitStack() as stack:
            scheduler = stack.enter_context(background(command, env, ROOT))
            stack.enter_context(
                background(
                    [
                        sys.executable,
                        "-m",
                        "celery",
                        "-A",
                        "app.entrypoints.worker:celery_app",
                        "worker",
                        "--pool=solo",
                        "--loglevel=WARNING",
                    ],
                    env,
                    ROOT,
                )
            )
            wait_for(lambda: rows("SELECT id FROM runtime_task_runs WHERE status='succeeded'"))
            duplicate = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=10)
            assert duplicate.returncode != 0
            rows(
                "UPDATE runtime_task_plans SET enabled=true, next_at=now() "
                "WHERE id='example.runtime.subscription'"
            )

            def connected():
                return rows(
                    "SELECT id, result FROM runtime_task_runs "
                    "WHERE task_id='example.runtime.subscription' AND status='running' "
                    "AND result->>'connection'='connected'"
                )

            run = wait_for(connected)[0]
            pid = json.loads(run["result"])["pid"]
            assert json.loads(run["result"])["parent_pid"] == scheduler.pid
            local = load_settings(path)
            redis = Redis.from_url(local.redis.url.get_secret_value())
            try:
                redis.publish(local.redis_prefix + "runtime:example", "message")
                wait_for(
                    lambda: rows(
                        "SELECT id FROM runtime_task_plans WHERE checkpoint->>'messages'='1'"
                    )
                )
                # The child can die without taking down its scheduler or losing its checkpoint.
                os.kill(pid, signal.SIGKILL)
                next_run = wait_for(
                    lambda: [r for r in connected() if r["id"] != run["id"]], timeout=40
                )[0]
                assert scheduler.poll() is None
                assert len(connected()) == 1
                assert (
                    rows(
                        "SELECT checkpoint FROM runtime_task_plans "
                        "WHERE id='example.runtime.subscription'"
                    )[0]["checkpoint"]
                    == '{"messages": 1}'
                )
                rows(
                    "UPDATE runtime_task_plans SET enabled=false "
                    "WHERE id='example.runtime.subscription'"
                )
                wait_for(lambda: not connected(), timeout=15)
                assert (
                    rows(f"SELECT status FROM runtime_task_runs WHERE id='{next_run['id']}'")[0][
                        "status"
                    ]
                    == "stopped"
                )
                rows(
                    "UPDATE runtime_task_plans SET enabled=true,next_at=now() "
                    "WHERE id='example.runtime.subscription'"
                )
                wait_for(connected)
                # Losing ownership stops the parent and its connection; it must not renew
                # or delete the new owner's token while shutting down.
                lease_key = local.redis_prefix + "scheduler:settings"
                redis.set(lease_key, "replacement-owner", px=30000)
                scheduler.wait(timeout=20)
                assert scheduler.returncode != 0
                assert redis.get(lease_key) == b"replacement-owner"
                wait_for(lambda: not connected(), timeout=10)
                redis.delete(lease_key)
                scheduler = stack.enter_context(background(command, env, ROOT))
                wait_for(connected)
                # Plugin activation is a separate gate from the per-task switch.
                rows(
                    "UPDATE site_settings SET data=jsonb_set(data, '{extensions}', "
                    '\'{"disabled_plugins":["example.runtime"]}\'::jsonb) WHERE id=1'
                )
                wait_for(lambda: not connected(), timeout=15)
                rows(
                    "UPDATE site_settings SET data=jsonb_set(data, '{extensions}', "
                    "'{\"disabled_plugins\":[]}'::jsonb) WHERE id=1"
                )
                wait_for(connected)
                scheduler.terminate()
                scheduler.wait(timeout=20)
                wait_for(lambda: not connected(), timeout=10)
            finally:
                keys = list(redis.scan_iter(local.redis_prefix + "*"))
                if keys:
                    redis.delete(*keys)
                redis.close()
    finally:

        async def drop():
            conn = await asyncpg.connect(
                base.set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
