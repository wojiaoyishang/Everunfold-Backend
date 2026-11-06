"""Real HTTP page source, deployment registration and public/draft separation boundaries."""

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import Database
from app.modules.content.repository import ContentRepository
from app.modules.content.service import PublicContentService
from app.modules.pages.models import FrontendPageRegistration, ManagedPage
from app.modules.pages.registration import install_manifest
from app.modules.pages.schemas import PageManifest
from tests.test_accounts import PASSWORD, AccountsTest, headers
from tests.test_accounts import accounts as accounts_fixture

accounts = accounts_fixture


@pytest.fixture
def pages(accounts: AccountsTest) -> Iterator[AccountsTest]:
    yield accounts

    async def clean() -> None:
        db = Database(accounts.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                await session.execute(
                    delete(ManagedPage).where(
                        ManagedPage.data["title"].astext.startswith(accounts.name)
                    )
                )
                await session.execute(
                    delete(FrontendPageRegistration).where(
                        FrontendPageRegistration.page_id.startswith(accounts.name)
                    )
                )
                await session.commit()
        finally:
            await db.close()

    asyncio.run(clean())


def body(ctx: AccountsTest, **value: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "value": {
            "title": ctx.name,
            "path": "/" + ctx.name,
            "html": "<h2>Public hello</h2>",
            **value,
        },
    }


def published(ctx: AccountsTest) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                value = await PublicContentService(ContentRepository(session)).snapshot()
                return [page.model_dump(mode="json") for page in value.pages]
        finally:
            await db.close()

    return asyncio.run(read())


def test_page_draft_apply_retry_conflict_disable_and_restore(pages: AccountsTest) -> None:
    ctx = pages
    draft = body(ctx)
    first = ctx.root.post("/v1/admin/pages/save", json=draft, headers=headers(ctx.root))
    assert first.status_code == 200, first.text
    row = first.json()["data"]
    assert not row["enabled"]
    assert all(p["id"] != row["id"] for p in published(ctx))
    assert (
        ctx.root.post("/v1/admin/pages/save", json=draft, headers=headers(ctx.root)).json()
        == first.json()
    )
    active = {
        **draft,
        "expected_revision": 1,
        "operation_id": str(uuid4()),
        "apply": True,
        "value": {**draft["value"], "enabled": True},
    }
    result = ctx.root.post("/v1/admin/pages/save", json=active, headers=headers(ctx.root))
    assert result.status_code == 200, result.text
    assert next(p for p in published(ctx) if p["id"] == row["id"])["html"] == draft["value"]["html"]
    newer = {
        **active,
        "operation_id": str(uuid4()),
        "expected_revision": 2,
        "apply": False,
        "value": {**active["value"], "html": "<p>Unpublished change</p>"},
    }
    result = ctx.root.post("/v1/admin/pages/save", json=newer, headers=headers(ctx.root))
    assert result.status_code == 200
    assert next(p for p in published(ctx) if p["id"] == row["id"])["html"] == draft["value"]["html"]
    assert (
        ctx.root.post(
            "/v1/admin/pages/save",
            json={**active, "operation_id": str(uuid4())},
            headers=headers(ctx.root),
        ).status_code
        == 409
    )
    collision = body(ctx, path=draft["value"]["path"])
    assert (
        ctx.root.post("/v1/admin/pages/save", json=collision, headers=headers(ctx.root)).status_code
        == 409
    )
    remove = {"id": row["id"], "expected_revision": 3, "operation_id": str(uuid4())}
    removed = ctx.root.post("/v1/admin/pages/remove", json=remove, headers=headers(ctx.root))
    assert removed.status_code == 200 and removed.json()["data"]["archived"]
    assert all(p["id"] != row["id"] for p in published(ctx))
    restored = ctx.root.post(
        "/v1/admin/pages/remove",
        json={**remove, "expected_revision": 4, "operation_id": str(uuid4()), "restore": True},
        headers=headers(ctx.root),
    )
    assert restored.status_code == 200 and not restored.json()["data"]["enabled"]
    assert restored.json()["data"]["value"]["html"] == "<p>Unpublished change</p>"


def test_page_script_password_csrf_and_session_boundaries(pages: AccountsTest) -> None:
    ctx = pages
    request = body(ctx, javascript='document.body.dataset.ran="yes";', javascript_enabled=True)
    assert (
        ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root)).json()[
            "code"
        ]
        == 40361
    )
    request["current_password"] = PASSWORD
    assert ctx.root.post("/v1/admin/pages/save", json=request).status_code == 403
    assert (
        ctx.root.post(
            "/v1/admin/pages/save",
            json=request,
            headers={**headers(ctx.root), "Authorization": "Bearer invalid"},
        ).json()["code"]
        == 40362
    )
    result = ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root))
    assert result.status_code == 200, result.text
    assert not result.json()["data"]["enabled"]
    for path in ["/admin/captured", "/account", "/search", "//external.example", "/a/../bad"]:
        attempt = body(ctx, path=path)
        assert (
            ctx.root.post(
                "/v1/admin/pages/save", json=attempt, headers=headers(ctx.root)
            ).status_code
            == 422
        )


def test_code_page_deployment_options_and_node_versions(pages: AccountsTest) -> None:
    ctx = pages
    identity = ctx.name + ".people"
    manifest = {
        "pages": [
            {
                "id": identity,
                "version": "1.0.0",
                "title": ctx.name + " code",
                "path": "/" + ctx.name + "-people",
                "fields": [
                    {
                        "key": "people",
                        "label": "人物",
                        "kind": "list",
                        "default": [],
                        "fields": [
                            {"key": "name", "label": "姓名", "kind": "text", "max_length": 30}
                        ],
                    }
                ],
            }
        ]
    }

    async def install() -> str:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                for node in ctx.settings.frontends:
                    await install_manifest(
                        session, ctx.settings, node, PageManifest.model_validate(manifest)
                    )
                return str(
                    await session.scalar(
                        select(ManagedPage.id).where(ManagedPage.implementation_id == identity)
                    )
                )
        finally:
            await db.close()

    identifier = asyncio.run(install())
    row = next(
        r for r in ctx.root.get("/v1/admin/pages").json()["data"]["items"] if r["id"] == identifier
    )
    assert row["available"] and row["source"] == "registered" and not row["enabled"]
    request: dict[str, Any] = {
        "id": identifier,
        "expected_revision": 1,
        "operation_id": str(uuid4()),
        "apply": True,
        "value": {
            **row["value"],
            "enabled": True,
            "options": {"people": [{"name": "Test person"}]},
        },
    }
    invalid = {
        **request,
        "operation_id": str(uuid4()),
        "value": {**request["value"], "html": "<script>unsafe()</script>"},
    }
    assert (
        ctx.root.post("/v1/admin/pages/save", json=invalid, headers=headers(ctx.root)).status_code
        == 403
    )
    invalid = {
        **request,
        "operation_id": str(uuid4()),
        "value": {**request["value"], "options": {"unknown": True}},
    }
    assert (
        ctx.root.post("/v1/admin/pages/save", json=invalid, headers=headers(ctx.root)).status_code
        == 422
    )
    result = ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root))
    assert result.status_code == 200, result.text
    assert (
        next(p for p in published(ctx) if p["id"] == identifier)["options"]["people"][0]["name"]
        == "Test person"
    )
