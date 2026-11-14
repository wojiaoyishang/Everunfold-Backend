"""A native package round trips through two API replicas and an isolated PostgreSQL database."""

import asyncio
import copy
import os
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
import pytest
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.modules.maintenance.schemas import MAX_BUNDLE_BYTES
from tests.test_auth_http_processes import free_port, uvicorn_process
from tests.test_settings_processes import toml_text

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/v1/admin/maintenance/"
ORIGIN = "http://127.0.0.1:3000"


def bundle_item(
    kind: str, title: str, value: dict[str, Any], *, identity: str | None = None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": identity or str(uuid4()),
        "title": title,
        "status": "published",
        "updated_at": datetime.now(UTC).isoformat(),
        "value": value,
    }


def checked(response: httpx.Response) -> Any:
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    return response.json()["data"]


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Isolated database required"
)
def test_native_bundle_preview_and_replay(tmp_path: Path) -> None:
    test_config = ROOT / "config.test.local.toml"
    original = load_settings(test_config)
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    database_name = "everunfold_maintenance_test_" + uuid4().hex[:12]
    url = original_url.set(database=database_name)
    port_a, port_b = free_port(), free_port()
    api_a, api_b = (f"http://127.0.0.1:{port}" for port in (port_a, port_b))
    raw = tomllib.loads(test_config.read_text())
    raw.update(BACKEND_BASE_URL=api_a, frontends={})
    raw["media"] = {"uploads_path": str(tmp_path / "uploads")}
    raw["database"]["url"] = url.render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "test-maintenance-" + uuid4().hex[:12]
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

    async def drop() -> None:
        dsn = original_url.set(drivername="postgresql", database="postgres")
        connection = await asyncpg.connect(dsn.render_as_string(hide_password=False))
        try:
            await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
        finally:
            await connection.close()

    try:
        init = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=50,
        )
        assert init.returncode == 0, "Maintenance test database initialization failed"
        with ExitStack() as stack:
            stack.enter_context(uvicorn_process(port_a, env))
            stack.enter_context(uvicorn_process(port_b, env))
            admin = stack.enter_context(
                httpx.Client(
                    base_url=api_a, headers={"Origin": ORIGIN}, trust_env=False, timeout=15
                )
            )
            assert admin.get(PREFIX + "health").status_code == 401
            session = checked(admin.get("/v1/auth/session"))
            account = checked(
                admin.post(
                    "/v1/auth/login",
                    json={"username": "admin", "password": "123456"},
                    headers={"X-CSRF-Token": session["csrf_token"]},
                )
            )
            csrf = {"X-CSRF-Token": account["csrf_token"]}
            assert admin.post(PREFIX + "export", json={"kinds": ["site"]}).status_code == 403
            health = checked(admin.get(PREFIX + "health"))
            assert {check["id"] for check in health["checks"]} == {
                "database",
                "redis",
                "queue",
                "media",
                "mail",
                "query_cache",
            }
            assert health["checks"][0]["status"] == "ok"

            category = bundle_item(
                "category", "Maintenance", {"name": "Maintenance", "slug": "maintenance"}
            )
            template = bundle_item(
                "template",
                "Portable template",
                {"title": "Portable template", "html": "<p>Template</p>"},
            )
            article_id, page_id, term_id = (str(uuid4()) for _ in range(3))
            article = bundle_item(
                "article",
                "Portable article",
                {
                    "language": "zh-CN",
                    "group_id": article_id,
                    "source_id": article_id,
                    "fields": {
                        "title": "Portable article",
                        "body": [{"id": str(uuid4()), "content": "Authored draft " * 4500}],
                        "settings": {
                            "path": "/maintenance-portable.html",
                            "categories": ["Maintenance"],
                        },
                    },
                },
                identity=article_id,
            )
            page = bundle_item(
                "page",
                "Portable page",
                {
                    "type_id": "core.custom",
                    "language": "zh-CN",
                    "group_id": page_id,
                    "source_id": page_id,
                    "template_id": template["id"],
                    "value": {
                        "title": "Portable page",
                        "path": "/maintenance-native-page.html",
                        "enabled": True,
                    },
                },
                identity=page_id,
            )
            term = bundle_item(
                "glossary",
                "Portable term",
                {
                    "language": "*",
                    "slug": "maintenance-term",
                    "title": "Portable term",
                    "body": [{"id": term_id, "content": "Term body"}],
                    "status": "published",
                },
            )
            parent = bundle_item(
                "comment",
                "Reader",
                {
                    "target_kind": "page",
                    "target_id": page_id,
                    "author": "Reader",
                    "body": "Parent",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            child = bundle_item(
                "comment",
                "Reader",
                {
                    "target_kind": "page",
                    "target_id": page_id,
                    "parent_id": parent["id"],
                    "author": "Reader",
                    "body": "**Reply**",
                    "body_format": "markdown",
                    "website": "https://example.org/",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            missing = bundle_item(
                "comment",
                "Reader",
                {
                    "target_kind": "content",
                    "target_id": article_id,
                    "author": "Reader",
                    "body": "Blocked until publish",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            site = bundle_item("site", "Settings", {"site": {"name": "Do not change settings"}})
            package: dict[str, Any] = {
                "format": "everunfold-content",
                "version": 1,
                "exported_at": datetime.now(UTC).isoformat(),
                "items": [child, page, article, term, category, template, parent, site, missing],
            }
            plan = checked(
                admin.post(PREFIX + "import/preview", json={"bundle": package}, headers=csrf)
            )
            assert plan["counts"] == {"create": 7, "skip": 0, "conflict": 0, "blocked": 2}
            assert checked(admin.get(PREFIX + "history"))["items"] == []
            tampered = copy.deepcopy(package)
            tampered["items"][0]["value"]["body"] = "Changed after preview"
            assert (
                admin.post(
                    PREFIX + "import/apply",
                    json={"bundle": tampered, "digest": plan["digest"]},
                    headers=csrf,
                ).status_code
                == 409
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda origin: checked(
                            httpx.post(
                                origin + PREFIX + "import/apply",
                                json={"bundle": package, "digest": plan["digest"]},
                                cookies=admin.cookies,
                                headers={**csrf, "Origin": ORIGIN},
                                timeout=15,
                            )
                        ),
                        (api_a, api_b),
                    )
                )
            assert results[0] == results[1]
            assert checked(admin.get(PREFIX + "history"))["items"] == [results[0]]

            exported = checked(
                admin.post(
                    PREFIX + "export",
                    headers=csrf,
                    json={
                        "kinds": [
                            "article",
                            "page",
                            "glossary",
                            "category",
                            "comment",
                            "template",
                            "site",
                        ],
                    },
                )
            )
            entries = {item["id"]: item for item in exported["items"]}
            for record in (article, page, parent, child, template, term):
                assert entries[record["id"]]["status"] == "draft"
            assert entries[child["id"]]["value"]["body_format"] == "markdown"
            assert entries[child["id"]]["value"]["website"] == "https://example.org/"
            assert entries[page_id]["value"]["value"]["enabled"] is False
            assert entries[article_id]["value"]["fields"].get("access_password") is None
            assert "Do not change settings" not in str(exported)
            second = checked(
                admin.post(PREFIX + "import/preview", json={"bundle": package}, headers=csrf)
            )
            assert second["counts"]["skip"] == 7, second["items"]
            conflict = copy.deepcopy(package)
            conflict["items"][2]["value"]["fields"]["title"] = "Overwrite attempt"
            third = checked(
                admin.post(PREFIX + "import/preview", json={"bundle": conflict}, headers=csrf)
            )
            assert (
                next(row for row in third["items"] if row["id"] == article_id)["state"]
                == "conflict"
            )
            too_large = admin.post(
                PREFIX + "import/preview",
                content=b" " * (MAX_BUNDLE_BYTES + 1025),
                headers={**csrf, "Content-Type": "application/json"},
            )
            assert too_large.status_code == 413
    finally:
        asyncio.run(drop())
