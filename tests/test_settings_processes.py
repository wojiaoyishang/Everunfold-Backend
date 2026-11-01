"""P0: real API replicas, PostgreSQL, Redis, Celery, Scheduler and Node receiver."""

import asyncio
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
import pytest
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from tests.test_auth_http_processes import free_port, uvicorn_process

ROOT = Path(__file__).resolve().parents[1]


def toml_text(data: dict[str, Any], prefix: str = "") -> str:
    scalar = [
        f"{key} = {json.dumps(value, ensure_ascii=False)}"
        for key, value in data.items()
        if not isinstance(value, dict)
    ]
    result = ([f"[{prefix}]"] if prefix else []) + scalar
    for key, value in data.items():
        if isinstance(value, dict):
            result.append(toml_text(value, f"{prefix}.{key}" if prefix else key))
    return "\n".join(result) + "\n"


@contextmanager
def background(arguments: list[str], env: dict[str, str], cwd: Path):  # type: ignore[no-untyped-def]
    process = subprocess.Popen(
        arguments, env=env, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=8)


def wait_for(check: Any, timeout: float = 25) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    pytest.fail("Timed out waiting for settings process state")


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1",
    reason="Explicit test database and frontend receiver required",
)
def test_settings_atomic_save_restart_delivery_and_crash_replay(tmp_path: Path) -> None:
    frontend = Path(
        os.environ.get(
            "EVERUNFOLD_TEST_FRONTEND", ROOT.parent.parent / "Node" / "Everunfold-Frontend"
        )
    )
    if not (frontend / "tools/fixtures/settings-receiver.mts").exists():
        pytest.skip("Set EVERUNFOLD_TEST_FRONTEND to the matching frontend checkout")
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    database_name = "everunfold_settings_test_" + uuid4().hex[:12]
    url = original_url.set(database=database_name)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    node_id = "settings-test"
    port_a, port_b, receiver_port = free_port(), free_port(), free_port()
    api_a, api_b = f"http://127.0.0.1:{port_a}", f"http://127.0.0.1:{port_b}"
    token = uuid4().hex + uuid4().hex
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text(encoding="utf-8"))
    raw["database"]["url"] = url.render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "test-settings-" + uuid4().hex[:10]
    raw["frontends"] = {
        node_id: {
            "base_url": "http://127.0.0.1:3000",
            "enabled": True,
            "settings_receiver_url": f"http://127.0.0.1:{receiver_port}",
            "nextjs_to_fastapi_token_env": "TEST_REVERSE_TOKEN",
            "fastapi_to_nextjs_token_env": "TEST_SETTINGS_TOKEN",
        }
    }
    raw["outbox"] = {
        "poll_interval_seconds": 1,
        "batch_size": 10,
        "connect_timeout_seconds": 1,
        "request_timeout_seconds": 1,
        "lease_seconds": 1,
        "max_attempts": 8,
        "retry_max_delay_seconds": 2,
    }
    config_file = tmp_path / "backend.local.toml"
    config_file.write_text(toml_text(raw), encoding="utf-8")
    env = {
        **os.environ,
        "CONFIG_FILE": str(config_file),
        "TEST_SETTINGS_TOKEN": token,
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("DATABASE_URL", None)
    env.pop("REDIS_URL", None)
    env.pop("EVERUNFOLD_ADMIN_PASSWORD", None)
    env.pop("EVERUNFOLD_ADMIN_USERNAME", None)

    async def sql(statement: str, *args: Any) -> Any:
        connection = await asyncpg.connect(dsn)
        try:
            return await connection.fetch(statement, *args)
        finally:
            await connection.close()

    try:
        initialized = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
        )
        assert initialized.returncode == 0, "Isolated database initialization failed"
        epoch = str(
            asyncio.run(sql("SELECT source_epoch FROM site_settings WHERE id=1"))[0]["source_epoch"]
        )
        node_config = tmp_path / "frontend.local.toml"
        node_config.write_text(
            toml_text(
                {
                    "BACKEND_BASE_URL": api_a,
                    "FRONTEND_NODE_ID": node_id,
                    "backend": {"allowed_peer_ips": ["127.0.0.1"]},
                    "settings": {
                        "directory": str(tmp_path / "applied"),
                        "source_epoch": epoch,
                        "receiver_port": receiver_port,
                    },
                }
            ),
            encoding="utf-8",
        )
        node_env = {**env, "CONFIG_FILE": str(node_config), "NODE_ENV": "test"}
        node_executable = shutil.which("node")
        assert node_executable
        node_command = [node_executable, "--import", "tsx", "tools/fixtures/settings-receiver.mts"]
        worker_command = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.entrypoints.worker:celery_app",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=WARNING",
            "--without-heartbeat",
            "--without-mingle",
            "--without-gossip",
        ]
        with ExitStack() as stack:
            stack.enter_context(uvicorn_process(port_a, env))
            stack.enter_context(uvicorn_process(port_b, env))
            client = stack.enter_context(
                httpx.Client(
                    base_url=api_a,
                    headers={"Origin": "http://127.0.0.1:3000"},
                    timeout=8,
                    trust_env=False,
                )
            )
            assert client.get("/v1/admin/settings").status_code == 401
            session = client.get("/v1/auth/session").json()["data"]
            result = client.post(
                "/v1/auth/login",
                json={"username": "admin", "password": "123456"},
                headers={"X-CSRF-Token": session["csrf_token"]},
            )
            assert result.status_code == 200
            csrf = {"X-CSRF-Token": result.json()["data"]["csrf_token"]}
            original_state = client.get("/v1/admin/settings").json()["data"]
            value = copy.deepcopy(original_state["value"])
            value["site"]["name"] = "Settings transaction"
            payload = {"expected_revision": original_state["revision"], "value": value}
            assert client.patch("/v1/admin/settings", json=payload).status_code == 403
            assert (
                client.patch(
                    "/v1/admin/settings",
                    json=payload,
                    headers={**csrf, "Origin": "https://untrusted.invalid"},
                ).status_code
                == 403
            )
            # Force an audit failure: the settings update AND delivery intent must roll back.
            asyncio.run(
                sql(
                    "ALTER TABLE settings_audit ADD CONSTRAINT test_audit_failure "
                    "CHECK (revision < 0)"
                )
            )
            assert client.patch("/v1/admin/settings", json=payload, headers=csrf).status_code == 503
            assert (
                client.get("/v1/admin/settings").json()["data"]["revision"]
                == original_state["revision"]
            )
            assert (
                asyncio.run(sql("SELECT count(*) AS count FROM settings_delivery_events"))[0][
                    "count"
                ]
                == 0
            )
            asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT test_audit_failure"))
            # Competing replicas, same revision. Exactly one save wins.
            cookies = dict(client.cookies)

            def save(origin: str) -> httpx.Response:
                return httpx.patch(
                    origin + "/v1/admin/settings",
                    json=payload,
                    cookies=cookies,
                    headers={**csrf, "Origin": "http://127.0.0.1:3000"},
                    timeout=10,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(save, [api_a, api_b]))
            assert sorted(response.status_code for response in responses) == [200, 409]
            assert asyncio.run(sql("SELECT count(*) AS count FROM settings_audit"))[0]["count"] == 1
            assert (
                asyncio.run(sql("SELECT count(*) AS count FROM settings_delivery_events"))[0][
                    "count"
                ]
                == 1
            )
            revision = original_state["revision"] + 1
            scheduler = stack.enter_context(
                background([sys.executable, "-m", "app.entrypoints.scheduler"], env, ROOT)
            )
            worker = stack.enter_context(background(worker_command, env, ROOT))

            def status() -> str:
                state = client.get("/v1/admin/settings").json()["data"]
                return str(state["delivery"][0]["status"])

            wait_for(lambda: status() == "failed")  # Receiver offline, database still saved.
            assert (
                client.get("/v1/admin/settings").json()["data"]["value"]["site"]["name"]
                == value["site"]["name"]
            )
            receiver = stack.enter_context(
                background(node_command, {**node_env, "TEST_ACK_DELAY_MS": "20000"}, frontend)
            )
            applied_file = (
                tmp_path / "applied" / node_id / epoch / f"public-settings.{revision}.json"
            )
            wait_for(applied_file.exists)
            # Worker dies after the receiver commits, before it receives a durable ACK.
            worker.terminate()
            worker.wait(timeout=8)
            receiver.terminate()
            receiver.wait(timeout=8)
            stack.enter_context(background(node_command, node_env, frontend))
            assert json.loads(applied_file.read_text(encoding="utf-8"))["revision"] == revision
            stack.enter_context(background(worker_command, env, ROOT))
            wait_for(lambda: status() == "applied", timeout=25)
            assert len(list(applied_file.parent.glob(f"public-settings.{revision}.json"))) == 1
            state = client.get(f"/v1/admin/frontend-nodes/{node_id}/cache-policy").json()["data"]
            new_policy = {
                **state["value"],
                "request_timeout_seconds": 3,
                "dynamic_freshness_seconds": 12,
            }
            result = client.patch(
                f"/v1/admin/frontend-nodes/{node_id}/cache-policy",
                json={"expected_revision": state["revision"], "value": new_policy},
                headers=csrf,
            )
            assert result.status_code == 200
            wait_for(
                lambda: (
                    client.get(f"/v1/admin/frontend-nodes/{node_id}/cache-policy").json()["data"][
                        "delivery"
                    ]["status"]
                    == "applied"
                )
            )
            policy_file = applied_file.parent / f"cache-policy.{state['revision'] + 1}.json"
            assert json.loads(policy_file.read_text(encoding="utf-8"))["value"] == new_policy
            # Losing an acknowledged node file must be repairable without a new business revision.
            applied_file.unlink()
            repaired = client.post(f"/v1/admin/frontend-nodes/{node_id}/sync", headers=csrf)
            assert repaired.status_code == 200
            wait_for(applied_file.exists)
            wait_for(lambda: status() == "applied")
            assert client.get("/v1/admin/settings").json()["data"]["revision"] == revision
            assert scheduler.poll() is None
            assert "no-store" in result.headers["cache-control"]
    finally:
        # Only this test's freshly created database is eligible for cleanup.
        assert database_name.startswith("everunfold_settings_test_")

        async def cleanup() -> None:
            connection = await asyncpg.connect(
                original_url.set(drivername="postgresql", database="postgres").render_as_string(
                    hide_password=False
                )
            )
            try:
                await connection.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
                    database_name,
                )
                await connection.execute('DROP DATABASE IF EXISTS "' + database_name + '"')
            finally:
                await connection.close()

        asyncio.run(cleanup())
