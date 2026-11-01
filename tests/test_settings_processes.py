"""P0: real API replicas, PostgreSQL, Redis, Celery, Scheduler and Node receiver."""

import asyncio
import base64
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
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
import pytest
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from tests.content_draft_process_checks import check_content_drafts
from tests.content_publication_process_checks import check_content_publication
from tests.media_library_process_checks import check_media_library
from tests.media_process_checks import check_media_management
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
    raw["media"] = {"uploads_path": str(tmp_path / "uploads")}
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
                    "cache": {"directory": str(tmp_path / "content")},
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
            value["site"]["notice_title"] = "Delivery notice"
            value["site"]["notice"] = "First line\nSecond line"
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
            restored_receiver = stack.enter_context(background(node_command, node_env, frontend))
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
            # Real authenticated uploads through two API processes share one immutable original.
            svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1h4v4z"/></svg>'
            upload = {
                "filename": "integration.svg",
                "upload_id": str(uuid4()),
                "data_base64": base64.b64encode(svg).decode(),
            }
            assert client.post("/v1/admin/icons/upload", json=upload).status_code == 403
            bad = {**upload, "data_base64": base64.b64encode(b'<svg onload="alert(1)"/>').decode()}
            assert client.post("/v1/admin/icons/upload", json=bad, headers=csrf).status_code == 422
            assert (
                httpx.post(
                    api_b + "/v1/admin/icons/query",
                    json={},
                    headers={"Origin": "http://127.0.0.1:3000"},
                ).status_code
                == 401
            )
            # The file exists, but ready and audit must roll back together.
            asyncio.run(
                sql(
                    "ALTER TABLE settings_audit ADD CONSTRAINT media_audit_failure "
                    "CHECK (resource NOT LIKE 'media:%')"
                )
            )
            assert (
                client.post("/v1/admin/icons/upload", json=upload, headers=csrf).status_code == 503
            )
            staging = asyncio.run(sql("SELECT * FROM media_assets"))[0]
            assert staging["status"] == "staging"
            assert client.get("/media/" + staging["object_key"]).status_code == 404
            assert (tmp_path / "uploads" / staging["object_key"]).read_bytes() == svg
            asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT media_audit_failure"))

            def retry_icon(origin: str) -> httpx.Response:
                return httpx.post(
                    origin + "/v1/admin/icons/upload",
                    json=upload,
                    cookies=cookies,
                    headers={**csrf, "Origin": "http://127.0.0.1:3000"},
                    timeout=10,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                uploads = list(executor.map(retry_icon, [api_a, api_b]))
            assert [response.status_code for response in uploads] == [200, 200]
            icon = uploads[0].json()["data"]
            assert uploads[1].json()["data"]["id"] == icon["id"]
            assert asyncio.run(sql("SELECT count(*) AS count FROM media_assets"))[0]["count"] == 1
            assert (
                asyncio.run(
                    sql(
                        "SELECT count(*) AS count FROM settings_audit WHERE resource LIKE 'media:%'"
                    )
                )[0]["count"]
                == 1
            )
            served = client.get(urlsplit(icon["url"]).path)
            assert served.content == svg and served.headers["x-content-type-options"] == "nosniff"
            assert "sandbox" in served.headers["content-security-policy"]
            assert (
                client.post(
                    "/v1/admin/icons/upload",
                    json={**upload, "filename": "changed.svg"},
                    headers=csrf,
                ).status_code
                == 409
            )
            catalog = client.post(
                "/v1/admin/icons/query", json={"query": "integration"}, headers=csrf
            )
            assert catalog.status_code == 200 and catalog.json()["data"]["total"] == 1
            assert "no-store" in catalog.headers["cache-control"]
            # A deeper menu and media mapping travel through the real delivery worker and receiver.
            menu: dict[str, Any] = {
                "id": "leaf",
                "label": "Deep",
                "href": "/deep",
                "icon": icon["id"],
            }
            for depth in range(8):
                menu = {"id": f"parent-{depth}", "label": "Parent", "href": "/", "children": [menu]}
            state = client.get("/v1/admin/settings").json()["data"]
            state["value"]["site"]["navigation"] = [menu]
            state["value"]["icon_urls"] = {icon["id"]: "https://forged.invalid/evil.svg"}
            result = client.patch(
                "/v1/admin/settings",
                json={"expected_revision": state["revision"], "value": state["value"]},
                headers=csrf,
            )
            assert result.status_code == 200
            assert result.json()["data"]["value"]["icon_urls"][icon["id"]] == icon["url"]
            wait_for(lambda: status() == "applied")
            latest = applied_file.parent / f"public-settings.{state['revision'] + 1}.json"
            assert (
                json.loads(latest.read_text(encoding="utf-8"))["value"]["icon_urls"][icon["id"]]
                == icon["url"]
            )

            # A valid HTTP body must not commit an undeliverable settings projection.
            current = client.get("/v1/admin/settings").json()["data"]
            oversized = copy.deepcopy(current["value"])
            oversized["site"]["navigation"] = [
                {"id": f"wide-{n}", "label": "Item", "href": "/" + "p" * 910} for n in range(64)
            ]
            encoded = json.dumps(
                {"expected_revision": current["revision"], "value": oversized},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            assert len(encoded) < 65536
            counts_query = (
                "SELECT (SELECT count(*) FROM settings_audit) AS audits, "
                "(SELECT count(*) FROM settings_delivery_events) AS events"
            )
            counts_before = asyncio.run(sql(counts_query))
            rejected = client.patch(
                "/v1/admin/settings",
                content=encoded,
                headers={**csrf, "Content-Type": "application/json"},
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["code"] == 42232
            assert (
                client.get("/v1/admin/settings").json()["data"]["revision"] == current["revision"]
            )
            assert asyncio.run(sql(counts_query)) == counts_before

            # Binary uploads are authenticated, idempotent and usable through typed media fields.
            media_metadata = {"filename": "integration-preview.mp4", "upload_id": str(uuid4())}
            video_bytes = (ROOT / "tests/fixtures/library-preview.mp4").read_bytes()
            binary_headers = {**csrf, "Content-Type": "application/octet-stream"}
            assert (
                httpx.post(
                    api_a + "/v1/admin/media/upload",
                    params=media_metadata,
                    content=video_bytes,
                    headers={
                        "Origin": "http://127.0.0.1:3000",
                        "Content-Type": "application/octet-stream",
                    },
                ).status_code
                == 401
            )
            assert (
                client.post(
                    "/v1/admin/media/upload",
                    params=media_metadata,
                    content=video_bytes,
                    headers={"Content-Type": "application/octet-stream"},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    "/v1/admin/media/upload",
                    params=media_metadata,
                    content=video_bytes,
                    headers={**csrf, "Content-Type": "text/plain"},
                ).status_code
                == 415
            )
            assert (
                client.post(
                    "/v1/admin/media/upload",
                    params=media_metadata,
                    content=b"not video",
                    headers=binary_headers,
                ).status_code
                == 422
            )

            def upload_video(origin: str) -> httpx.Response:
                return httpx.post(
                    origin + "/v1/admin/media/upload",
                    params=media_metadata,
                    content=video_bytes,
                    headers={**binary_headers, "Origin": "http://127.0.0.1:3000"},
                    cookies=cookies,
                    timeout=10,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                videos = list(executor.map(upload_video, [api_a, api_b]))
            assert [response.status_code for response in videos] == [200, 200]
            video = videos[0].json()["data"]
            assert video["kind"] == "video" and videos[1].json()["data"]["id"] == video["id"]
            assert asyncio.run(sql("SELECT count(*) AS count FROM media_assets"))[0]["count"] == 2
            assert (
                asyncio.run(
                    sql(
                        "SELECT count(*) AS count FROM settings_audit WHERE resource = $1",
                        video["id"],
                    )
                )[0]["count"]
                == 1
            )
            ranged = client.get(urlsplit(video["url"]).path, headers={"Range": "bytes=0-31"})
            assert ranged.status_code == 206 and ranged.content == video_bytes[:32]
            images = client.post(
                "/v1/admin/media/query", json={"kind": "image"}, headers=csrf
            ).json()["data"]
            assert [item["id"] for item in images["items"]] == [icon["id"]]
            assert (
                client.post("/v1/admin/media/query", json={"kind": "video"}, headers=csrf).json()[
                    "data"
                ]["items"][0]["id"]
                == video["id"]
            )
            current = client.get("/v1/admin/settings").json()["data"]
            media_value = copy.deepcopy(current["value"])
            media_value["site"].update(
                {"avatar_media_id": icon["id"], "avatar_url": "https://forged.invalid/avatar.svg"}
            )
            media_value["theme"].update(
                {
                    "background_media_id": icon["id"],
                    "background_video_media_id": video["id"],
                    "background_url": "https://forged.invalid/background.svg",
                    "background_video_url": "https://forged.invalid/background.mp4",
                }
            )
            wrong_kind = copy.deepcopy(media_value)
            wrong_kind["theme"]["background_video_media_id"] = icon["id"]
            assert (
                client.patch(
                    "/v1/admin/settings",
                    json={"expected_revision": current["revision"], "value": wrong_kind},
                    headers=csrf,
                ).status_code
                == 422
            )
            bound = client.patch(
                "/v1/admin/settings",
                json={"expected_revision": current["revision"], "value": media_value},
                headers=csrf,
            )
            assert bound.status_code == 200, bound.text
            bound_value = bound.json()["data"]["value"]
            assert bound_value["site"]["avatar_url"] == icon["url"]
            assert bound_value["theme"]["background_url"] == icon["url"]
            assert bound_value["theme"]["background_video_url"] == video["url"]
            wait_for(lambda: status() == "applied")
            bound_revision = bound.json()["data"]["revision"]
            bound_file = applied_file.parent / f"public-settings.{bound_revision}.json"
            assert json.loads(bound_file.read_text(encoding="utf-8"))["value"] == bound_value

            check_media_management(client, csrf, cookies, api_a, api_b, sql)
            check_media_library(client, csrf, cookies, api_a, api_b, sql, tmp_path / "uploads")
            check_content_drafts(client, csrf, cookies, api_a, api_b, sql)
            check_content_publication(
                client,
                csrf,
                cookies,
                api_a,
                api_b,
                sql,
                wait_for,
                restored_receiver,
                lambda: stack.enter_context(background(node_command, node_env, frontend)),
                f"http://127.0.0.1:{receiver_port}",
                token,
                frontend,
                node_executable,
                node_env,
                config_file,
            )

            # CDN origin changes republish a new version, never overwrite a prior ACKed file.
            raw["media"]["base_url"] = "https://cdn.example.test/assets"
            config_file.write_text(toml_text(raw), encoding="utf-8")
            port_c = free_port()
            with uvicorn_process(port_c, env):
                republished = httpx.post(
                    f"http://127.0.0.1:{port_c}/v1/admin/frontend-nodes/{node_id}/sync",
                    cookies=cookies,
                    headers={**csrf, "Origin": "http://127.0.0.1:3000"},
                    timeout=10,
                )
                assert republished.status_code == 200, republished.text
                wait_for(lambda: status() == "applied")
            cdn_file = applied_file.parent / f"public-settings.{bound_revision + 1}.json"
            cdn_value = json.loads(cdn_file.read_text(encoding="utf-8"))["value"]
            assert cdn_value["icon_urls"][icon["id"]].startswith("https://cdn.example.test/assets/")
            assert cdn_value["site"]["avatar_url"].startswith("https://cdn.example.test/assets/")
            assert cdn_value["theme"]["background_video_url"].startswith(
                "https://cdn.example.test/assets/"
            )
            assert json.loads(bound_file.read_text(encoding="utf-8"))["value"] == bound_value
            assert (
                json.loads(latest.read_text(encoding="utf-8"))["value"]["icon_urls"][icon["id"]]
                == icon["url"]
            )
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
