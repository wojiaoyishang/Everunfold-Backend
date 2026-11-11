"""P0: two API replicas, a separate Node child and real queued signed callbacks."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
import pytest
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.modules.auth.application_signatures import signed_headers, verify_signature
from tests.account_access_process_checks import check_scoped_accounts
from tests.preset_process_checks import check_user_presets
from tests.test_auth_http_processes import free_port, uvicorn_process
from tests.test_settings_processes import background, toml_text, wait_for

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:3000"


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Isolated database required"
)
def test_live_account_authority_and_child_callbacks(tmp_path: Path) -> None:
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text())
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    database_name = "everunfold_accounts_test_" + uuid4().hex[:12]
    url = original_url.set(database=database_name)
    port_a, port_b, child_port = free_port(), free_port(), free_port()
    api_a, api_b, child = (f"http://127.0.0.1:{p}" for p in (port_a, port_b, child_port))
    raw.update(BACKEND_BASE_URL=api_a, frontends={})
    raw["media"] = {"uploads_path": str(tmp_path / "uploads")}
    raw["database"]["url"] = url.render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "test-accounts-" + uuid4().hex[:12]
    raw.setdefault("auth", {}).update(callback_allowed_origins=[child])
    raw["outbox"] = {"request_timeout_seconds": 1, "max_attempts": 2, "retry_max_delay_seconds": 1}
    config_file = tmp_path / "backend.toml"
    config_file.write_text(toml_text(raw))
    env = {
        **os.environ,
        "CONFIG_FILE": str(config_file),
        "EVERUNFOLD_API_APPLICATION_MASTER_KEY": "process_" + "x" * 43,
    }
    for name in (
        "DATABASE_URL",
        "REDIS_URL",
        "EVERUNFOLD_ADMIN_PASSWORD",
        "EVERUNFOLD_ADMIN_USERNAME",
    ):
        env.pop(name, None)

    async def sql(statement: str, *args: Any) -> Any:
        connection = await asyncpg.connect(
            url.set(drivername="postgresql").render_as_string(hide_password=False)
        )
        try:
            return await connection.fetch(statement, *args)
        finally:
            await connection.close()

    try:
        init = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=40,
        )
        assert init.returncode == 0, "Account test database initialization failed"
        with ExitStack() as stack:
            stack.enter_context(uvicorn_process(port_a, env))
            stack.enter_context(uvicorn_process(port_b, env))
            admin = stack.enter_context(
                httpx.Client(base_url=api_a, headers={"Origin": ORIGIN}, trust_env=False, timeout=8)
            )
            session = admin.get("/v1/auth/session").json()["data"]
            login = admin.post(
                "/v1/auth/login",
                json={"username": "admin", "password": "123456"},
                headers={"X-CSRF-Token": session["csrf_token"]},
            )
            assert login.status_code == 200
            csrf = {"X-CSRF-Token": login.json()["data"]["csrf_token"]}
            app_response = admin.post(
                "/v1/admin/api-key-applications",
                headers=csrf,
                json={
                    "operation_id": str(uuid4()),
                    "app_id": "child",
                    "audience": "everunfold",
                    "current_password": "123456",
                    "value": {
                        "name": "P0 child",
                        "callback_url": child + "/callback",
                        "scopes": [{"code": "content:read", "label": "Read", "owner_scope": True}],
                    },
                },
            )
            assert app_response.status_code == 201, app_response.text
            app = app_response.json()["data"]
            child_config = tmp_path / "child.json"
            child_config.write_text(
                json.dumps(
                    {
                        "port": child_port,
                        "appId": "child",
                        "audience": "everunfold",
                        "queryUrl": app["application"]["query_url"],
                        "queryKeyId": app["application"]["query_key_id"],
                        "callbackKeyId": app["application"]["callback_key_id"],
                        "querySecret": app["query_secret"],
                        "callbackSecret": app["callback_secret"],
                        "callbackUrl": child + "/callback",
                        "cacheSeconds": 2,
                    }
                )
            )
            node = shutil.which("node")
            assert node
            stack.enter_context(
                background([node, "tests/fixtures/api-key-child.mjs", str(child_config)], env, ROOT)
            )

            def ready() -> bool:
                try:
                    return httpx.get(child + "/health", timeout=0.5).status_code == 200
                except httpx.TransportError:
                    return False

            wait_for(ready)
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
            stack.enter_context(background(worker_command, env, ROOT))
            stack.enter_context(
                background([sys.executable, "-m", "app.entrypoints.scheduler"], env, ROOT)
            )

            def key() -> dict[str, Any]:
                response = admin.post(
                    "/v1/account/api-keys",
                    headers=csrf,
                    json={
                        "operation_id": str(uuid4()),
                        "app_id": "child",
                        "name": "P0 read",
                        "current_password": "123456",
                        "permissions": {"content:read": "all"},
                        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    },
                )
                assert response.status_code == 201, response.text
                return dict(response.json()["data"])

            def check(token: str, *, write: bool = False) -> httpx.Response:
                return httpx.post(
                    child + "/check", json={"token": token, "write": write}, timeout=8
                )

            def revoke(item: dict[str, Any]) -> None:
                result = admin.post(
                    api_b + "/v1/account/api-keys/revoke",
                    headers=csrf,
                    json={"operation_id": str(uuid4()), "key_id": item["key"]["id"]},
                )
                assert result.status_code == 200, result.text

            first = key()
            assert check(first["secret"]).json()["active"]
            before = httpx.get(child + "/stats").json()["queries"]
            assert check(first["secret"]).json()["active"]
            assert httpx.get(child + "/stats").json()["queries"] == before
            assert check(first["secret"], write=True).json()["active"]
            assert httpx.get(child + "/stats").json()["queries"] == before + 1
            # Failed notification cannot extend a read lease. Writes always confirm online.
            httpx.post(child + "/control", json={"mode": "unavailable"})
            revoke(first)
            assert not check(first["secret"], write=True).json()["active"]
            time.sleep(2.1)
            assert not check(first["secret"]).json()["active"]
            wait_for(
                lambda: (
                    asyncio.run(
                        sql(
                            "SELECT status FROM auth_callback_events "
                            "ORDER BY created_at DESC LIMIT 1"
                        )
                    )[0]["status"]
                    == "failed"
                )
            )
            httpx.post(child + "/control", json={"mode": "normal"})
            retry = admin.post(
                "/v1/admin/api-key-applications/child/callback",
                headers=csrf,
                json={"operation_id": str(uuid4()), "action": "retry"},
            )
            assert retry.status_code == 200
            wait_for(
                lambda: (
                    asyncio.run(
                        sql(
                            "SELECT status FROM auth_callback_events "
                            "ORDER BY created_at DESC LIMIT 1"
                        )
                    )[0]["status"]
                    == "delivered"
                )
            )
            # A delayed positive introspection response must not repopulate after its callback.
            second = key()
            httpx.post(child + "/control", json={"delay": 1800})
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(check, second["secret"])
                time.sleep(0.25)
                revoke(second)
                wait_for(
                    lambda: (
                        asyncio.run(
                            sql(
                                "SELECT status FROM auth_callback_events "
                                "ORDER BY created_at DESC LIMIT 1"
                            )
                        )[0]["status"]
                        == "delivered"
                    )
                )
                delayed = pending.result()
                assert (
                    delayed.status_code == 503
                    and delayed.json()["error"] == "authorization_changed_retry"
                )
            httpx.post(child + "/control", json={})
            assert not check(second["secret"]).json()["active"]
            # The independent receiver acknowledges duplicates and old versions monotonically.
            old = {
                "protocol": "everunfold.api-key.v1",
                "event_id": str(uuid4()),
                "app_id": "child",
                "audience": "everunfold",
                "type": "application.resync",
                "app_version": 1,
                "occurred_at": datetime.now(UTC).isoformat(),
            }
            body = json.dumps(old).encode()
            for _ in range(2):
                proof = signed_headers(
                    app["callback_secret"],
                    "child",
                    "child:callback:1",
                    "POST",
                    child + "/callback",
                    body,
                )
                receipt = httpx.post(child + "/callback", content=body, headers=proof)
                assert receipt.status_code == 200 and receipt.json()["app_version"] >= 3
                assert (
                    verify_signature(
                        app["callback_secret"],
                        "child",
                        "child:callback:1",
                        "RESPONSE",
                        child + "/callback",
                        receipt.content,
                        receipt.headers,
                    )
                    == proof["X-Everunfold-Nonce"]
                )
                assert (
                    httpx.post(child + "/callback", content=body, headers=proof).status_code == 503
                )
            # HTTP 200 without a signed ACK never becomes delivered; manual retry repairs it.
            httpx.post(child + "/control", json={"mode": "badack"})
            command = admin.post(
                "/v1/admin/api-key-applications/child/callback",
                headers=csrf,
                json={"operation_id": str(uuid4()), "action": "test"},
            )
            assert command.status_code == 200
            wait_for(
                lambda: (
                    asyncio.run(
                        sql(
                            "SELECT status FROM auth_callback_events "
                            "ORDER BY created_at DESC LIMIT 1"
                        )
                    )[0]["status"]
                    == "failed"
                )
            )
            assert (
                asyncio.run(
                    sql(
                        "SELECT last_error FROM auth_callback_events "
                        "ORDER BY created_at DESC LIMIT 1"
                    )
                )[0]["last_error"]
                == "invalid_signature"
            )
            httpx.post(child + "/control", json={})
            stack.enter_context(background(worker_command, env, ROOT))
            command = admin.post(
                "/v1/admin/api-key-applications/child/callback",
                headers=csrf,
                json={"operation_id": str(uuid4()), "action": "retry"},
            )
            assert command.status_code == 200
            wait_for(
                lambda: (
                    asyncio.run(
                        sql(
                            "SELECT status FROM auth_callback_events "
                            "ORDER BY created_at DESC LIMIT 1"
                        )
                    )[0]["status"]
                    == "delivered"
                )
            )
            # Last-administrator protection spans both API processes, including self-disable.
            check_scoped_accounts(admin, csrf, api_b)
            check_user_presets(admin, csrf, api_b)
            created = admin.post(
                "/v1/admin/users",
                headers=csrf,
                json={
                    "operation_id": str(uuid4()),
                    "username": "second_admin",
                    "display_name": "Second",
                    "password": "Temporary-pass-12345",
                    "role": "admin",
                },
            )
            assert created.status_code == 201
            other = stack.enter_context(
                httpx.Client(base_url=api_b, headers={"Origin": ORIGIN}, timeout=8, trust_env=False)
            )
            initial = other.get("/v1/auth/session").json()["data"]
            logged = other.post(
                "/v1/auth/login",
                headers={"X-CSRF-Token": initial["csrf_token"]},
                json={"username": "second_admin", "password": "Temporary-pass-12345"},
            ).json()["data"]
            profile = other.get("/v1/account/profile").json()["data"]
            changed = other.post(
                "/v1/account/password",
                headers={"X-CSRF-Token": logged["csrf_token"]},
                json={
                    "operation_id": str(uuid4()),
                    "expected_revision": profile["revision"],
                    "current_password": "Temporary-pass-12345",
                    "new_password": "Permanent-pass-12345",
                },
            )
            assert changed.status_code == 200, changed.text

            def disable(pair: tuple[httpx.Client, str]) -> httpx.Response:
                client, password = pair
                data = client.get("/v1/account/profile").json()["data"]
                token = client.get("/v1/auth/session").json()["data"]["csrf_token"]
                fields = {
                    name: data[name]
                    for name in (
                        "display_name",
                        "email",
                        "avatar_url",
                        "bio",
                        "website",
                        "preferred_locale",
                    )
                }
                return client.patch(
                    "/v1/admin/users/" + data["id"],
                    headers={"X-CSRF-Token": token},
                    json={
                        "operation_id": str(uuid4()),
                        "expected_revision": data["revision"],
                        "value": fields,
                        "role": "admin",
                        "active": False,
                        "grants": {},
                    },
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(
                    pool.map(disable, [(admin, "123456"), (other, "Permanent-pass-12345")])
                )
            assert sorted(response.status_code for response in responses) == [200, 409], [
                r.text for r in responses
            ]
            assert (
                asyncio.run(sql("SELECT count(*) AS n FROM users WHERE active AND role='admin'"))[
                    0
                ]["n"]
                == 1
            )
    finally:

        async def drop() -> None:
            connection = await asyncpg.connect(
                original_url.set(drivername="postgresql", database="postgres").render_as_string(
                    hide_password=False
                )
            )
            try:
                await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
            finally:
                await connection.close()

        asyncio.run(drop())
