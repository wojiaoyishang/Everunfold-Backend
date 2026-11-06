"""Real HTTP page source, deployment registration and public/draft separation boundaries."""

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from app.core.database import Database
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.models import User
from app.modules.content.repository import ContentRepository
from app.modules.content.service import PublicContentService
from app.modules.pages.models import FrontendPageRegistration, ManagedPage
from app.modules.pages.registration import install_manifest
from app.modules.pages.schemas import PageManifest
from tests.test_accounts import PASSWORD, AccountsTest, headers, signed_in
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
    row = ctx.root.get("/v1/admin/pages/" + identifier).json()["data"]
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


def test_delegated_page_write_does_not_grant_publish_or_script_execution(
    pages: AccountsTest,
) -> None:
    ctx = pages
    name = ctx.name + "_page_writer"
    asyncio.run(seed_admin(ctx.settings, name, PASSWORD))

    async def delegate() -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                await session.execute(
                    update(User)
                    .where(User.username == name)
                    .values(
                        role="user",
                        grants={"everunfold": {"pages:read": "all", "pages:write": "all"}},
                    )
                )
                await session.commit()
        finally:
            await db.close()

    asyncio.run(delegate())
    member = ctx.client()
    signed_in(member, name)
    request = body(ctx)
    saved = member.post("/v1/admin/pages/save", json=request, headers=headers(member))
    assert saved.status_code == 200, saved.text
    assert member.get("/v1/admin/pages/" + request["id"]).status_code == 200
    denied = member.post(
        "/v1/admin/pages/save",
        json={
            **request,
            "expected_revision": 1,
            "operation_id": str(uuid4()),
            "apply": True,
            "value": {**request["value"], "enabled": True},
        },
        headers=headers(member),
    )
    assert denied.status_code == 403
    assert ctx.root.get("/v1/admin/pages/" + request["id"]).json()["data"]["revision"] == 1
    script = body(
        ctx, path="/" + ctx.name + "-script", javascript_enabled=True, javascript="alert(1)"
    )
    script["current_password"] = PASSWORD
    assert (
        member.post("/v1/admin/pages/save", json=script, headers=headers(member)).status_code == 403
    )
    assert ctx.root.get("/v1/admin/pages/" + script["id"]).status_code == 404


def test_registered_pages_ignore_retired_nodes_and_reserve_a_safe_initial_path(
    pages: AccountsTest,
) -> None:
    ctx = pages
    existing = body(ctx)
    assert (
        ctx.root.post("/v1/admin/pages/save", json=existing, headers=headers(ctx.root)).status_code
        == 200
    )
    manifest = PageManifest.model_validate(
        {
            "pages": [
                {
                    "id": ctx.name + ".collision",
                    "version": "1.0.0",
                    "title": ctx.name + " code collision",
                    "path": existing["value"]["path"],
                }
            ]
        }
    )

    async def install() -> str:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                for node in ctx.settings.frontends:
                    await install_manifest(session, ctx.settings, node, manifest)
                session.add(
                    FrontendPageRegistration(
                        node_id="retired-node",
                        page_id=manifest.pages[0].id,
                        definition={
                            **manifest.pages[0].model_dump(mode="json"),
                            "version": "9.0.0",
                        },
                    )
                )
                await session.commit()
                return str(
                    await session.scalar(
                        select(ManagedPage.id).where(
                            ManagedPage.implementation_id == manifest.pages[0].id
                        )
                    )
                )
        finally:
            await db.close()

    identity = asyncio.run(install())
    row = ctx.root.get("/v1/admin/pages/" + identity).json()["data"]
    assert row["available"] and row["value"]["path"] != existing["value"]["path"]
    assert row["registration"]["version"] == "1.0.0"


def test_private_page_metadata_has_its_own_revision_and_never_enters_public_data(
    pages: AccountsTest,
) -> None:
    ctx = pages
    request = body(ctx, enabled=True)
    request["apply"] = True
    saved = ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root)).json()[
        "data"
    ]
    url = "/v1/admin/pages/" + saved["id"] + "/metadata"
    first = ctx.root.get(url)
    assert first.status_code == 200 and first.json()["data"]["revision"] == 0
    assert "no-store" in first.headers["cache-control"]
    assert ctx.client().get(url).status_code == 401
    payload = {
        "expected_revision": 0,
        "operation_id": str(uuid4()),
        "value": {"import_cursor": "PRIVATE_METADATA_MARKER", "internal_ids": [1, 2, 3]},
    }
    assert ctx.root.patch(url, json=payload).status_code == 403
    updated = ctx.root.patch(url, json=payload, headers=headers(ctx.root))
    assert updated.status_code == 200 and updated.json()["data"]["revision"] == 1
    assert ctx.root.patch(url, json=payload, headers=headers(ctx.root)).json() == updated.json()
    assert (
        ctx.root.patch(
            url, json={**payload, "operation_id": str(uuid4())}, headers=headers(ctx.root)
        ).status_code
        == 409
    )
    assert (
        ctx.root.patch(
            url,
            json={**payload, "operation_id": str(uuid4()), "value": {"oversized": "x" * 65536}},
            headers=headers(ctx.root),
        ).status_code
        == 413
    )
    nonfinite = (
        '{"expected_revision":1,"operation_id":"' + str(uuid4()) + '","value":{"number":NaN}}'
    )
    assert (
        ctx.root.patch(
            url,
            content=nonfinite,
            headers={**headers(ctx.root), "Content-Type": "application/json"},
        ).status_code
        == 422
    )
    detail = ctx.root.get("/v1/admin/pages/" + saved["id"])
    assert detail.json()["data"]["revision"] == saved["revision"]
    assert "PRIVATE_METADATA_MARKER" not in detail.text
    assert "PRIVATE_METADATA_MARKER" not in ctx.root.get("/v1/admin/pages").text
    assert "PRIVATE_METADATA_MARKER" not in str(published(ctx))
    assert ctx.root.get(url).json()["data"]["value"] == payload["value"]


def test_page_contract_matches_published_source() -> None:
    import json
    from pathlib import Path

    from tools.export_pages_contract import build_schema

    assert json.loads(Path("contracts/pages-v1.schema.json").read_text()) == build_schema()


def test_page_source_can_exceed_small_settings_transport_limit(pages: AccountsTest) -> None:
    payload = body(pages, html="<p>" + "x" * 70000 + "</p>")
    response = pages.root.post("/v1/admin/pages/save", json=payload, headers=headers(pages.root))
    assert response.status_code == 200, response.text
    assert response.json()["data"]["value"]["html"] == payload["value"]["html"]


def test_active_frontend_version_mismatch_blocks_only_application(pages: AccountsTest) -> None:
    from fastapi.testclient import TestClient

    from app.entrypoints.api import create_app

    ctx = pages
    first = next(iter(ctx.settings.frontends.values()))
    config = ctx.settings.model_copy(
        update={"frontends": {**ctx.settings.frontends, "required-secondary": first.model_copy()}}
    )
    manifest = PageManifest.model_validate(
        {
            "pages": [
                {
                    "id": ctx.name + ".versions",
                    "version": "1.0.0",
                    "title": ctx.name + " versions",
                    "path": "/" + ctx.name + "-versions",
                }
            ]
        }
    )

    async def install() -> str:
        db = Database(config.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                for node in config.frontends:
                    await install_manifest(session, config, node, manifest)
                changed = manifest.model_copy(deep=True)
                changed.pages[0].version = "2.0.0"
                await install_manifest(session, config, "required-secondary", changed)
                return str(
                    await session.scalar(
                        select(ManagedPage.id).where(
                            ManagedPage.implementation_id == manifest.pages[0].id
                        )
                    )
                )
        finally:
            await db.close()

    identity = asyncio.run(install())
    with TestClient(create_app(config)) as client:
        signed_in(client, ctx.name)
        row = client.get("/v1/admin/pages/" + identity).json()["data"]
        assert not row["available"]
        payload = {
            "id": identity,
            "expected_revision": row["revision"],
            "operation_id": str(uuid4()),
            "apply": True,
            "value": {**row["value"], "enabled": True},
        }
        rejected = client.post("/v1/admin/pages/save", json=payload, headers=headers(client))
        assert rejected.status_code == 409 and rejected.json()["code"] == 40998
        draft = client.post(
            "/v1/admin/pages/save",
            json={**payload, "operation_id": str(uuid4()), "apply": False},
            headers=headers(client),
        )
        assert draft.status_code == 200 and not draft.json()["data"]["enabled"]
