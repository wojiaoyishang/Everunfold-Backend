"""Private task arguments survive durable dispatch and are validated again by Worker."""

import asyncio
import json
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
from app.core.jobs.definitions import TaskDefinition, definitions
from app.core.jobs.models import TaskPlan, TaskRun
from app.core.jobs.parameters import checked_json, normalized_parameters, parameters_schema
from app.core.jobs.progress_models import TaskProgressEventRecord
from app.entrypoints.api import create_app
from app.modules.auth.bootstrap import seed_admin
from app.modules.settings.models import SettingsAudit
from app.plugins.runtime_example.parameter_task import ExampleParameters
from tests.test_accounts import ORIGIN, PASSWORD, headers, signed_in
from tests.test_settings_processes import background, toml_text, wait_for

TASK = "example.runtime.parameters"


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"count": "3"},
        {"count": True},
        {"count": 0},
        {"count": 21},
        {"count": 2, "enabled": "false"},
        {"count": 2, "mode": "invalid"},
        {"count": 2, "unknown": "private-input"},
        {"count": 2, "options": {"unknown": "private-input"}},
        {"count": 2, "options": {"weights": ["3"]}},
    ],
)
def test_task_models_reject_invalid_arguments(parameters):
    with pytest.raises(ValueError):
        normalized_parameters(definitions()[TASK][1], parameters)


def test_task_defaults_schema_and_json_boundaries():
    spec = definitions()[TASK][1]
    assert spec.default_parameters == {
        "count": 2,
        "enabled": True,
        "mode": "sum",
        "options": {"weights": [1, 2, 3], "labels": []},
    }
    schema = parameters_schema(spec)
    assert schema["required"] == ["count"] and schema["additionalProperties"] is False
    assert schema["$defs"]["CalculationOptions"]["additionalProperties"] is False
    with pytest.raises(ValueError):
        TaskDefinition(
            "test.invalid",
            "Invalid",
            "app.core.jobs.handlers:settings",
            parameters=ExampleParameters,
        )
    for value in ({"value": "x" * 32768}, {"value": float("nan")}, {"value": float("inf")}):
        with pytest.raises(ValueError):
            checked_json(value)
    nested = {}
    for _ in range(18):
        nested = {"nested": nested}
    with pytest.raises(ValueError):
        checked_json(nested)
    with pytest.raises(ValueError):
        normalized_parameters(definitions()["core.callbacks"][1], {"count": 3})


@pytest.mark.skipif(os.getenv("RUN_DATABASE_TESTS") != "1", reason="Explicit isolated database")
def test_parameters_private_serial_dispatch_defaults_and_worker_revalidation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = load_settings(root / "config.test.local.toml")
    base = make_url(original.database.url.get_secret_value())
    assert base.database == "everunfold_test"
    name = "everunfold_parameters_test_" + uuid4().hex[:10]
    raw = tomllib.loads((root / "config.test.local.toml").read_text())
    raw["database"]["url"] = base.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "parameters-test-" + uuid4().hex
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
    try:
        for command in (
            [sys.executable, "init.database.py", "--config", str(path)],
            [
                sys.executable,
                "-m",
                "tools.register_backend_plugins",
                "--config",
                str(path),
                "--apply",
            ],
        ):
            subprocess.run(command, cwd=root, env=env, check=True, capture_output=True)

        async def inspect():
            db = Database(config.database.url.get_secret_value())
            try:
                async with db.session_factory() as session:
                    runs = list(
                        await session.scalars(
                            select(TaskRun)
                            .where(TaskRun.task_id == TASK)
                            .order_by(TaskRun.planned_at)
                        )
                    )
                    audit = list(
                        await session.scalars(
                            select(SettingsAudit).where(SettingsAudit.resource == "runtime-task")
                        )
                    )
                    events = list(await session.scalars(select(TaskProgressEventRecord)))
                    return runs, audit, await session.get(TaskPlan, TASK), events
            finally:
                await db.close()

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

        asyncio.run(prepare())
        api_config = config.model_copy(update={"cors": CORSConfig(allowed_origins=[ORIGIN])})
        admin_name = "parameters_" + uuid4().hex
        asyncio.run(seed_admin(api_config, admin_name, PASSWORD))
        with ExitStack() as stack:
            client = stack.enter_context(
                TestClient(create_app(api_config), client=("parameters-test-" + uuid4().hex, 50000))
            )
            signed_in(client, admin_name)
            csrf = headers(client)
            overview = client.get("/v1/admin/runtime").json()["data"]
            selected = next(p for p in overview["plans"] if p["id"] == TASK)
            assert selected["parameters_schema"]["required"] == ["count"]
            assert selected["parameter_defaults"]["count"] == 2
            payload = {"task_id": TASK, "action": "debug_run"}
            for parameters in (
                {},
                {"count": "secret-invalid"},
                {"count": 3, "extra": "private-marker"},
                {"count": 3, "options": {"extra": "private-marker"}},
                {"count": 3, "options": {"labels": ["x" * 33000]}},
            ):
                response = client.post(
                    "/v1/admin/runtime/control",
                    json={**payload, "parameters": parameters},
                    headers=csrf,
                )
                assert response.status_code == 422
                assert (
                    "secret-invalid" not in response.text and "private-marker" not in response.text
                )
            assert (
                client.post(
                    "/v1/admin/runtime/control",
                    json={**payload, "action": "enable", "parameters": {"count": 3}},
                    headers=csrf,
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/v1/admin/runtime/control",
                    json={
                        "task_id": "core.callbacks",
                        "action": "debug_run",
                        "parameters": {"count": 3},
                    },
                    headers=csrf,
                ).status_code
                == 422
            )
            assert asyncio.run(inspect())[0] == []
            arguments = {
                "count": 3,
                "enabled": False,
                "mode": "product",
                "options": {"weights": [2, 5], "labels": ["private-marker"]},
            }
            payload["parameters"] = arguments
            assert client.post("/v1/admin/runtime/control", json=payload).status_code == 403
            with ThreadPoolExecutor(max_workers=4) as pool:
                responses = list(
                    pool.map(
                        lambda _: client.post(
                            "/v1/admin/runtime/control", json=payload, headers=csrf
                        ),
                        range(6),
                    )
                )
            assert all(r.status_code == 200 for r in responses)
            assert all("private-marker" not in r.text for r in responses)
            runs, audit, plan, _ = asyncio.run(inspect())
            assert len(runs) == 1 and runs[0].parameters == arguments
            assert plan.next_at == future and plan.enabled
            assert "private-marker" not in json.dumps([row.changes for row in audit])
            assert audit[-1].changes["parameter_keys"] == sorted(arguments)
            assert (
                client.post(
                    "/v1/admin/runtime/control",
                    json={**payload, "parameters": {**arguments, "count": 4}},
                    headers=csrf,
                ).status_code
                == 409
            )
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
            assert runs[0].result == {
                "completed": 3,
                "enabled": False,
                "mode": "product",
                "value": 1,
                "label_count": 1,
            }
            assert plan.next_at == future
            assert "private-marker" not in "\n".join(event.message for event in events)

            async def corrupt():
                db = Database(config.database.url.get_secret_value())
                try:
                    async with db.session_factory() as session:
                        row = TaskRun(
                            task_id=TASK,
                            planned_at=datetime.now(UTC),
                            parameters={"count": "private-corrupt-value"},
                        )
                        session.add(row)
                        await session.commit()
                        return row.id
                finally:
                    await db.close()

            invalid = asyncio.run(corrupt())
            wait_for(
                lambda: any(
                    row.id == invalid and row.status == "failed"
                    for row in asyncio.run(inspect())[0]
                ),
                timeout=35,
            )
            runs, _, _, events = asyncio.run(inspect())
            assert next(row for row in runs if row.id == invalid).error == "ValidationError"
            assert "private-corrupt-value" not in "\n".join(event.message for event in events)

            async def schedule_default():
                db = Database(config.database.url.get_secret_value())
                try:
                    async with db.session_factory() as session:
                        row = await session.get(TaskPlan, TASK)
                        row.next_at = datetime.now(UTC)
                        await session.commit()
                finally:
                    await db.close()

            asyncio.run(schedule_default())
            wait_for(
                lambda: (
                    len(asyncio.run(inspect())[0]) >= 3
                    and asyncio.run(inspect())[0][-1].status == "succeeded"
                ),
                timeout=35,
            )
            latest = asyncio.run(inspect())[0][-1]
            assert latest.parameters == definitions()[TASK][1].default_parameters
            assert latest.result["completed"] == 2 and latest.result["value"] == 6
    finally:

        async def cleanup():
            connection = await asyncpg.connect(
                base.set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await connection.close()

        asyncio.run(cleanup())
        redis = Redis.from_url(config.redis.url.get_secret_value())
        try:
            keys = list(redis.scan_iter(match=config.redis_prefix + "*"))
            if keys:
                redis.delete(*keys)
        finally:
            redis.close()
