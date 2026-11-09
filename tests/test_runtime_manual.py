"""An explicit developer trigger uses the registered Scheduler/Worker execution path."""

import asyncio
import os
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import CORSConfig, load_settings
from app.core.database import Database
from app.core.jobs.definitions import definitions
from app.core.jobs.models import TaskPlan, TaskRun
from app.core.jobs.progress_models import TaskProgressEventRecord
from app.entrypoints.api import create_app
from app.modules.auth.bootstrap import seed_admin
from app.modules.settings.models import SettingsAudit
from tests.test_accounts import ORIGIN, PASSWORD, headers, signed_in
from tests.test_settings_processes import background, toml_text, wait_for


@pytest.mark.skipif(os.getenv("RUN_DATABASE_TESTS") != "1", reason="Explicit isolated database")
def test_developer_trigger_is_authorized_serial_and_dispatched_by_scheduler(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = load_settings(root / "config.test.local.toml")
    base = make_url(original.database.url.get_secret_value())
    assert base.database == "everunfold_test"
    name = "everunfold_manual_test_" + uuid4().hex[:10]
    raw = tomllib.loads((root / "config.test.local.toml").read_text())
    raw["database"]["url"] = base.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "manual-test-" + uuid4().hex
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
    config = load_settings(path)
    future = datetime.now(UTC) + timedelta(days=1)
    task_id = "core.callbacks"
    try:
        subprocess.run(
            [sys.executable, "init.database.py", "--config", str(path)],
            cwd=root,
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
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )

        async def prepare():
            db = Database(config.database.url.get_secret_value())
            try:
                async with db.session_factory() as session:
                    for identity, (_, spec) in definitions().items():
                        session.add(
                            TaskPlan(
                                id=identity,
                                enabled=spec.kind == "periodic",
                                next_at=future,
                                checkpoint={},
                            )
                        )
                    await session.commit()
            finally:
                await db.close()

        async def inspect():
            db = Database(config.database.url.get_secret_value())
            try:
                async with db.session_factory() as session:
                    runs = list(
                        await session.scalars(select(TaskRun).where(TaskRun.task_id == task_id))
                    )
                    audit = list(
                        await session.scalars(
                            select(SettingsAudit).where(SettingsAudit.resource == "runtime-task")
                        )
                    )
                    plan = await session.get(TaskPlan, task_id)
                    events = list(
                        await session.scalars(
                            select(TaskProgressEventRecord).where(
                                TaskProgressEventRecord.resource == "runtime"
                            )
                        )
                    )
                    return runs, audit, plan, events
            finally:
                await db.close()

        asyncio.run(prepare())
        api_config = config.model_copy(update={"cors": CORSConfig(allowed_origins=[ORIGIN])})
        admin_name = "manual_" + uuid4().hex
        asyncio.run(seed_admin(api_config, admin_name, PASSWORD))
        with ExitStack() as stack:
            client = stack.enter_context(
                TestClient(create_app(api_config), client=("manual-test-" + uuid4().hex, 50000))
            )
            signed_in(client, admin_name)
            csrf = headers(client)
            payload = {"task_id": task_id, "action": "debug_run"}
            assert client.post("/v1/admin/runtime/control", json=payload).status_code == 403
            assert (
                client.post(
                    "/v1/admin/runtime/control",
                    json={**payload, "task_id": "os.system"},
                    headers=csrf,
                ).status_code
                == 409
            )
            resident = next(
                identity for identity, (_, spec) in definitions().items() if spec.kind == "resident"
            )
            assert (
                client.post(
                    "/v1/admin/runtime/control", json={**payload, "task_id": resident}, headers=csrf
                ).status_code
                == 403
            )
            overview = client.get("/v1/admin/runtime").json()["data"]
            selected = next(plan for plan in overview["plans"] if plan["id"] == task_id)
            assert selected["handler"] == "app.core.jobs.handlers:callbacks"
            assert selected["manual"] is False
            assert (
                client.post(
                    "/v1/admin/runtime/control", json={**payload, "action": "run"}, headers=csrf
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/v1/admin/runtime/control", json={**payload, "action": "disable"}, headers=csrf
                ).status_code
                == 200
            )
            assert (
                client.post("/v1/admin/runtime/control", json=payload, headers=csrf).status_code
                == 403
            )
            assert not asyncio.run(inspect())[2].enabled
            assert (
                client.post(
                    "/v1/admin/runtime/control", json={**payload, "action": "enable"}, headers=csrf
                ).status_code
                == 200
            )

            # Restore the next scheduled tick after explicitly enabling; debug must preserve it.
            async def restore_next():
                db = Database(config.database.url.get_secret_value())
                try:
                    async with db.session_factory() as session:
                        plan = await session.get(TaskPlan, task_id)
                        plan.next_at = future
                        await session.commit()
                finally:
                    await db.close()

            asyncio.run(restore_next())
            response = client.post(
                "/v1/account/api-keys",
                json={
                    "operation_id": str(uuid4()),
                    "app_id": "everunfold",
                    "name": "Read-only runtime",
                    "permissions": {"settings:read": "all"},
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    "current_password": PASSWORD,
                },
                headers=csrf,
            )
            assert response.status_code == 201, response.text
            assert (
                client.post(
                    "/v1/admin/runtime/control",
                    json=payload,
                    headers={"Authorization": "Bearer " + response.json()["data"]["secret"]},
                ).status_code
                == 403
            )
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(
                    pool.map(
                        lambda _: client.post(
                            "/v1/admin/runtime/control", json=payload, headers=csrf
                        ),
                        range(6),
                    )
                )
            assert all(response.status_code == 200 for response in results)
            runs, audit, plan, _ = asyncio.run(inspect())
            assert len(runs) == 1 and runs[0].status == "queued"
            identity = runs[0].id
            assert plan.enabled and plan.next_at == future
            debug = [row for row in audit if row.changes.get("action") == "debug_run"]
            assert len(debug) == 6 and sum(not row.changes["reused"] for row in debug) == 1
            assert {row.changes["run_id"] for row in debug} == {str(identity)}
            stack.enter_context(
                background([sys.executable, "-m", "app.entrypoints.scheduler"], env, root)
            )
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
                    root,
                )
            )
            wait_for(lambda: asyncio.run(inspect())[0][0].status == "succeeded", timeout=35)
            runs, _, plan, events = asyncio.run(inspect())
            assert len(runs) == 1 and runs[0].id == identity and runs[0].owner
            assert runs[0].sent_at and runs[0].started_at and runs[0].finished_at
            assert runs[0].result == {"completed": 0}
            assert plan.next_at == future and plan.enabled
            assert events and {event.resource_id for event in events} == {identity}
    finally:

        async def cleanup():
            conn = await asyncpg.connect(
                base.set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(cleanup())
        redis = Redis.from_url(config.redis.url.get_secret_value())
        try:
            keys = list(redis.scan_iter(match=config.redis_prefix + "*"))
            if keys:
                redis.delete(*keys)
        finally:
            redis.close()
