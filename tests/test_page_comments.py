"""Real page comment admission, applied snapshots and protected read boundaries."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.core.redis_keys import redis_key
from app.entrypoints.api import create_app
from app.modules.auth.bootstrap import seed_admin
from app.modules.content.models import SiteSettings
from app.modules.content.publication import ContentPublication
from app.modules.content.repository import ContentRepository
from app.modules.content.service import PublicContentService
from app.modules.notifications.service import NotificationService
from app.modules.pages.schemas import PageManifest
from app.modules.settings.extensions_schemas import ExtensionInventory
from app.modules.settings.extensions_service import install_inventory
from tests.test_accounts import (
    ORIGIN,
    PASSWORD,
    TEMPORARY,
    AccountsTest,
    create_user,
    headers,
    signed_in,
)
from tests.test_managed_pages import body
from tests.test_settings_processes import toml_text

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def comments(tmp_path: Path) -> Iterator[AccountsTest]:
    if os.environ.get("RUN_DATABASE_TESTS") != "1":
        pytest.skip("Dedicated PostgreSQL test database required")
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    name = "everunfold_pagecomments_test_" + uuid4().hex[:12]
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text())
    raw["database"]["url"] = original_url.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "test-pagecomments-" + uuid4().hex
    raw["media"] = {"uploads_path": str(tmp_path / "uploads")}
    config_file = tmp_path / "backend.local.toml"
    config_file.write_text(toml_text(raw))
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    env.pop("REDIS_URL", None)
    settings = load_settings(config_file)
    try:
        initialized = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert initialized.returncode == 0, "Isolated page-comment database initialization failed"
        username = "pagecomments_" + uuid4().hex
        asyncio.run(seed_admin(settings, username, PASSWORD))
        with ExitStack() as stack:
            peer = "page-comments-" + uuid4().hex
            client = stack.enter_context(TestClient(create_app(settings), client=(peer, 50000)))
            client.headers["Origin"] = ORIGIN
            signed_in(client, username)
            yield AccountsTest(settings, username, client, stack, peer)
    finally:

        async def clean() -> None:
            redis = Redis.from_url(settings.redis.url.get_secret_value())
            try:
                keys = [key async for key in redis.scan_iter(match=settings.redis_prefix + "*")]
                if keys:
                    await redis.delete(*keys)
            finally:
                await redis.aclose()
            connection = await asyncpg.connect(
                original_url.set(database="postgres", drivername="postgresql").render_as_string(
                    hide_password=False
                )
            )
            try:
                await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await connection.close()

        asyncio.run(clean())


def snapshot(ctx: AccountsTest) -> dict[str, Any]:
    async def read() -> dict[str, Any]:
        database = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                return (
                    await PublicContentService(ContentRepository(session)).snapshot()
                ).model_dump(mode="json")
        finally:
            await database.close()

    return asyncio.run(read())


def save(
    ctx: AccountsTest, row: dict[str, Any], *, apply: bool = True, **value: Any
) -> dict[str, Any]:
    response = ctx.root.post(
        "/v1/admin/pages/save",
        json={
            "id": row["id"],
            "operation_id": str(uuid4()),
            "expected_revision": row["revision"],
            "apply": apply,
            "value": {**row["value"], **value},
        },
        headers=headers(ctx.root),
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def registered(
    ctx: AccountsTest, *, comments_open: bool, existing_id: str | None = None
) -> dict[str, Any]:
    identity = "test.people"
    manifest = PageManifest.model_validate(
        {
            "pages": [
                {
                    "id": identity,
                    "title": "Code page",
                    "version": "1.0.0",
                    "path": "/people",
                    "comments_open": comments_open,
                }
            ]
        }
    )
    inventory = ExtensionInventory.model_validate(
        {
            "pages": manifest.model_dump(mode="json"),
            "plugins": [
                {"id": "test.people", "title": "People", "build": "a" * 64, "page_ids": [identity]}
            ],
        }
    )

    async def install() -> None:
        database = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                for node in ctx.settings.frontends:
                    await install_inventory(session, ctx.settings, node, inventory)
        finally:
            await database.close()

    asyncio.run(install())
    if existing_id:
        response = ctx.root.get("/v1/admin/pages/" + existing_id)
    else:
        response = ctx.root.post(
            "/v1/admin/pages/save",
            json={
                **body(ctx, path="/people", html="", comments_open=comments_open),
                "type_id": identity,
            },
            headers=headers(ctx.root),
        )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def custom(ctx: AccountsTest, **value: Any) -> dict[str, Any]:
    response = ctx.root.post(
        "/v1/admin/pages/save",
        json={**body(ctx, enabled=True, **value), "apply": True},
        headers=headers(ctx.root),
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def comment(page_id: str, **values: Any) -> dict[str, Any]:
    return {
        "operation_id": str(uuid4()),
        "client_id": str(uuid4()),
        "content_id": page_id,
        "author": "Reader",
        "body": "A real page comment",
        **values,
    }


def approve(ctx: AccountsTest, operation_id: str) -> None:
    response = ctx.root.post(
        "/v1/admin/comments/moderate",
        json={
            "operation_id": str(uuid4()),
            "items": [{"id": operation_id, "revision": 1}],
            "action": "approve",
        },
        headers=headers(ctx.root),
    )
    assert response.status_code == 200, response.text


def test_account_receives_approved_reply_once_in_private_inbox(comments: AccountsTest) -> None:
    ctx = comments
    target = custom(ctx)
    member = create_user(ctx)
    reader = ctx.client()
    signed_in(reader, member["username"], TEMPORARY)
    preferences = "/v1/account/notifications/preferences"
    assert reader.get(preferences).json()["data"] == {"comment_reply_email": False}
    assert (
        reader.patch(
            preferences, json={"comment_reply_email": True}, headers=headers(reader)
        ).status_code
        == 422
    )
    submission = comment(target["id"], author="Untrusted client label")
    route = "/v1/reader/comments"
    assert reader.post(route, json=submission, headers=headers(reader)).status_code == 200
    assert ctx.root.post(route, json=submission).status_code == 409
    approve(ctx, submission["operation_id"])
    reply = comment(target["id"], parent_id=submission["operation_id"])
    assert ctx.root.post(route, json=reply, headers=headers(ctx.root)).status_code == 200

    async def dispatch() -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                await NotificationService(session, ctx.settings).dispatch_events()
        finally:
            await db.close()

    inbox = "/v1/account/notifications/inbox"
    asyncio.run(dispatch())
    assert reader.get(inbox).json()["data"]["items"] == []
    approve(ctx, reply["operation_id"])
    for action, revision in [("reject", 2), ("approve", 3)]:
        response = ctx.root.post(
            "/v1/admin/comments/moderate",
            json={
                "operation_id": str(uuid4()),
                "items": [{"id": reply["operation_id"], "revision": revision}],
                "action": action,
            },
            headers=headers(ctx.root),
        )
        assert response.status_code == 200, response.text
    asyncio.run(dispatch())
    items = reader.get(inbox).json()["data"]["items"]
    href = target["value"]["path"] + "#comment-" + reply["operation_id"]
    assert [item["href"] for item in items] == [href]
    assert all(item["href"] != href for item in ctx.root.get(inbox).json()["data"]["items"])
    public_comments = snapshot(ctx)["comments"][target["id"]]
    assert public_comments[0]["author"] == member["display_name"]
    assert all("author_user_id" not in item for item in public_comments)


@pytest.mark.parametrize("source", ["custom", "registered"])
def test_page_comment_switch_applies_only_with_published_snapshot(
    comments: AccountsTest, source: str
) -> None:
    ctx = comments
    row = custom(ctx) if source == "custom" else registered(ctx, comments_open=False)
    if source == "registered":
        assert not row["value"]["comments_open"]
        row = save(ctx, row, enabled=True)
        denied = ctx.root.post("/v1/reader/comments", json=comment(row["id"]))
        assert denied.status_code == 403
        row = save(ctx, row, comments_open=True)
    else:
        assert row["value"]["comments_open"]
    if source == "registered":
        # Refreshing plugin defaults must not undo the administrator's applied choice.
        assert registered(ctx, comments_open=False, existing_id=row["id"])["value"]["comments_open"]
    request = comment(row["id"])
    accepted = ctx.root.post("/v1/reader/comments", json=request)
    assert accepted.status_code == 200, accepted.text
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 200
    pending = ctx.root.get(f"/v1/contents/{row['id']}/comments").json()["data"]
    assert pending["items"] == []
    approve(ctx, request["operation_id"])
    row = save(ctx, row, apply=False, comments_open=False)
    assert not row["value"]["comments_open"]
    public = next(page for page in snapshot(ctx)["pages"] if page["id"] == row["id"])
    assert public["comments_open"]
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 200
    row = save(ctx, row)
    assert not next(page for page in snapshot(ctx)["pages"] if page["id"] == row["id"])[
        "comments_open"
    ]
    for payload in (
        request,
        comment(row["id"]),
        comment(row["id"], parent_id=request["operation_id"]),
    ):
        denied = ctx.root.post("/v1/reader/comments", json=payload)
        assert denied.status_code == 403 and denied.json()["code"] == 40388
    visible = ctx.root.get(f"/v1/contents/{row['id']}/comments", params={"page_size": 1})
    assert visible.status_code == 200, visible.text
    assert visible.json()["data"]["total"] == 1
    assert visible.json()["data"]["items"][0]["id"] == request["operation_id"]
    assert len(snapshot(ctx)["comments"][row["id"]]) == 1
    for page, expected in ((1, 1), (2, 0)):
        paged = ctx.root.post(
            "/v1/reader/page-comments",
            json={"id": row["id"], "page": page, "page_size": 1},
        )
        assert paged.status_code == 200, paged.text
        assert paged.json()["data"]["total"] == 1
        assert len(paged.json()["data"]["items"]) == expected


def unlock(ctx: AccountsTest, page_id: str) -> dict[str, Any]:
    client_id = str(uuid4())
    challenge = ctx.root.post(
        "/v1/reader/captcha/challenge",
        json={"action": "read", "content_id": page_id, "client_id": client_id},
    )
    assert challenge.status_code == 200, challenge.text
    data = challenge.json()["data"]

    async def solve() -> None:
        redis = Redis.from_url(ctx.settings.redis.url.get_secret_value())
        try:
            key = redis_key(ctx.settings, "captcha", "challenge", data["challenge_id"])
            raw = await redis.get(key)
            assert raw
            stored = json.loads(raw)
            stored["digest"] = hashlib.sha256((stored["salt"] + "ABCDE").encode()).hexdigest()
            await redis.set(key, json.dumps(stored), ex=180)
        finally:
            await redis.aclose()

    asyncio.run(solve())
    response = ctx.root.post(
        "/v1/reader/access-page",
        json={
            "id": page_id,
            "captcha": {
                "challenge_id": data["challenge_id"],
                "client_id": client_id,
                "answer": "abcde",
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def test_protected_comments_require_current_read_receipt_even_on_retry(
    comments: AccountsTest,
) -> None:
    ctx = comments
    row = custom(ctx, captcha_read="required")
    request = comment(row["id"])
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 403
    opened = unlock(ctx, row["id"])
    request["read_receipt"] = opened["read_receipt"]
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 200
    assert (
        ctx.root.post("/v1/reader/comments", json={**request, "read_receipt": ""}).status_code
        == 403
    )
    approve(ctx, request["operation_id"])
    assert ctx.root.get(f"/v1/contents/{row['id']}/comments").status_code == 404
    assert ctx.root.post("/v1/reader/page-comments", json={"id": row["id"]}).status_code == 403
    assert row["id"] not in snapshot(ctx)["comments"]
    assert unlock(ctx, row["id"])["unlocked_comments"][0]["id"] == request["operation_id"]
    paged = ctx.root.post(
        "/v1/reader/page-comments",
        json={"id": row["id"], "read_receipt": request["read_receipt"], "page_size": 1},
    )
    assert paged.status_code == 200 and paged.json()["data"]["total"] == 1
    assert paged.json()["data"]["items"][0]["id"] == request["operation_id"]
    with TestClient(create_app(ctx.settings), client=("different-peer", 50001)) as outsider:
        wrong_peer = outsider.post(
            "/v1/reader/page-comments",
            json={"id": row["id"], "read_receipt": request["read_receipt"]},
            headers={"Origin": ORIGIN},
        )
        assert wrong_peer.status_code == 403
    other = custom(ctx, path="/another-protected", captcha_read="required")
    assert (
        ctx.root.post(
            "/v1/reader/page-comments",
            json={"id": other["id"], "read_receipt": request["read_receipt"]},
        ).status_code
        == 403
    )
    row = save(ctx, row, comments_open=False)
    closed = unlock(ctx, row["id"])
    assert not closed["comments_open"] and len(closed["unlocked_comments"]) == 1
    assert (
        ctx.root.post(
            "/v1/reader/page-comments",
            json={"id": row["id"], "read_receipt": opened["read_receipt"]},
        ).status_code
        == 403
    )
    assert (
        ctx.root.post(
            "/v1/reader/page-comments",
            json={"id": row["id"], "read_receipt": closed["read_receipt"]},
        ).json()["data"]["total"]
        == 1
    )
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 403
    row = save(ctx, row, comments_open=True)
    # Applying a new generation invalidates the old unlock proof, including idempotent retries.
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 403
    request["read_receipt"] = unlock(ctx, row["id"])["read_receipt"]
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 200
    save(ctx, row, enabled=False)
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 404
    assert (
        ctx.root.post(
            "/v1/reader/page-comments",
            json={"id": row["id"], "read_receipt": request["read_receipt"]},
        ).status_code
        == 404
    )


def test_disabled_page_plugin_hides_comments_and_blocks_submission_retries(
    comments: AccountsTest,
) -> None:
    ctx = comments
    row = save(ctx, registered(ctx, comments_open=True), enabled=True)
    request = comment(row["id"])
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 200
    approve(ctx, request["operation_id"])

    async def disable() -> None:
        database = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                site = await session.get(SiteSettings, 1)
                assert site
                site.data = {
                    **site.data,
                    "extensions": {
                        **site.data.get("extensions", {}),
                        "disabled_plugins": ["test.people"],
                    },
                }
                await session.flush()
                await ContentPublication(session, ctx.settings).stage()
                await session.commit()
        finally:
            await database.close()

    asyncio.run(disable())
    assert ctx.root.post("/v1/reader/comments", json=request).status_code == 404
    assert ctx.root.post("/v1/reader/comments", json=comment(row["id"])).status_code == 404
    assert ctx.root.get(f"/v1/contents/{row['id']}/comments").status_code == 404
    assert ctx.root.post("/v1/reader/page-comments", json={"id": row["id"]}).status_code == 404
    assert row["id"] not in snapshot(ctx)["comments"]
    assert all(page["id"] != row["id"] for page in snapshot(ctx)["pages"])
