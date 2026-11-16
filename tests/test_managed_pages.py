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
from app.modules.media.models import MediaAsset
from app.modules.pages.models import (
    FrontendPageRegistration,
    ManagedPage,
    PageTemplate,
    PageTranslationGroup,
)
from app.modules.pages.registration import install_manifest
from app.modules.pages.schemas import PageManifest
from app.modules.shortcodes.regions import RegionPassword, ShortcodeRegion
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
                page_ids = select(ManagedPage.id).where(
                    ManagedPage.data["title"].astext.startswith(accounts.name)
                )
                credentials = list(
                    await session.scalars(
                        select(ShortcodeRegion.credential).where(
                            ShortcodeRegion.owner_kind == "page",
                            ShortcodeRegion.owner_id.in_(page_ids),
                        )
                    )
                )
                await session.execute(
                    delete(ShortcodeRegion).where(
                        ShortcodeRegion.owner_kind == "page", ShortcodeRegion.owner_id.in_(page_ids)
                    )
                )
                await session.execute(
                    delete(RegionPassword).where(
                        RegionPassword.id.in_(
                            [identity for identity in credentials if identity is not None]
                        )
                    )
                )
                await session.execute(
                    delete(PageTranslationGroup).where(
                        PageTranslationGroup.id.in_(
                            select(ManagedPage.translation_group_id).where(
                                ManagedPage.data["title"].astext.startswith(accounts.name)
                            )
                        )
                    )
                )
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
                await session.execute(
                    delete(PageTemplate).where(PageTemplate.title.startswith(accounts.name))
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


def test_page_catalog_excludes_component_media_resolved_for_detail(pages: AccountsTest) -> None:
    media_id = uuid4()
    object_key = f"images/{media_id}.svg"
    payload = body(
        pages,
        html="",
        edit_mode="components",
        component_document={
            "version": 1,
            "nodes": [
                {
                    "id": "media",
                    "type": "core.markdown",
                    "props": {"source": f"![Cover](site://media/{media_id})"},
                }
            ],
        },
    )

    async def check() -> None:
        db = Database(pages.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                session.add(
                    MediaAsset(
                        id=media_id,
                        uploader_id=uuid4(),
                        purpose="library",
                        filename="cover.svg",
                        mime="image/svg+xml",
                        size=1,
                        sha256=media_id.hex * 2,
                        object_key=object_key,
                        status="ready",
                    )
                )
                await session.commit()
                try:
                    saved = pages.root.post(
                        "/v1/admin/pages/save", json=payload, headers=headers(pages.root)
                    )
                    assert saved.status_code == 200, saved.text
                    detail = pages.root.get(f"/v1/admin/pages/{payload['id']}")
                    assert detail.status_code == 200, detail.text
                    assert detail.json()["data"]["media_urls"][str(media_id)]["original"] == (
                        f"{pages.settings.media_base_url}/{object_key}"
                    )
                    catalog = pages.root.get("/v1/admin/pages")
                    assert catalog.status_code == 200, catalog.text
                    item = next(
                        row for row in catalog.json()["data"]["items"] if row["id"] == payload["id"]
                    )
                    assert "media_urls" not in item and item["path"] == payload["value"]["path"]
                finally:
                    await session.execute(delete(MediaAsset).where(MediaAsset.id == media_id))
                    await session.commit()
        finally:
            await db.close()

    asyncio.run(check())


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
    draft = body(ctx, javascript_position="head", seo_title="First SEO title", seo_max_snippet=0)
    first = ctx.root.post("/v1/admin/pages/save", json=draft, headers=headers(ctx.root))
    assert first.status_code == 200, first.text
    row = first.json()["data"]
    assert row["value"]["javascript_position"] == "head"
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
    assert next(p for p in published(ctx) if p["id"] == row["id"])["javascript_position"] == "head"
    assert next(p for p in published(ctx) if p["id"] == row["id"])["seo_max_snippet"] == 0
    newer = {
        **active,
        "operation_id": str(uuid4()),
        "expected_revision": 2,
        "apply": False,
        "value": {
            **active["value"],
            "html": "<p>Unpublished change</p>",
            "javascript_position": "body_end",
            "seo_title": "Unpublished SEO title",
        },
    }
    result = ctx.root.post("/v1/admin/pages/save", json=newer, headers=headers(ctx.root))
    assert result.status_code == 200
    assert result.json()["data"]["value"]["javascript_position"] == "body_end"
    assert next(p for p in published(ctx) if p["id"] == row["id"])["html"] == draft["value"]["html"]
    assert next(p for p in published(ctx) if p["id"] == row["id"])["javascript_position"] == "head"
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
    assert restored.json()["data"]["value"]["javascript_position"] == "body_end"


@pytest.mark.parametrize("position", ["head", "body_end"])
def test_page_script_admin_session_without_password_and_csrf_boundaries(
    pages: AccountsTest,
    position: str,
) -> None:
    ctx = pages
    request = body(
        ctx,
        javascript='document.documentElement.dataset.ran="yes";',
        javascript_enabled=True,
        javascript_position=position,
    )
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
    assert result.json()["data"]["applied_javascript_enabled"]
    assert not result.json()["data"]["enable_requires_password"]
    active = ctx.root.post(
        "/v1/admin/pages/enabled",
        json={
            "id": request["id"],
            "expected_revision": result.json()["data"]["revision"],
            "enabled": True,
            "apply_draft": True,
            "operation_id": str(uuid4()),
        },
        headers=headers(ctx.root),
    )
    assert active.status_code == 200, active.text
    assert (
        next(p for p in published(ctx) if p["id"] == request["id"])["javascript_position"]
        == position
    )
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
                "defaults": {"layout": {"columns": [1, 2, {"wide": True}], "optional": None}},
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

    async def install() -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                for node in ctx.settings.frontends:
                    await install_manifest(
                        session, ctx.settings, node, PageManifest.model_validate(manifest)
                    )
        finally:
            await db.close()

    asyncio.run(install())
    types = {
        row["id"]: row for row in ctx.root.get("/v1/admin/pages/types").json()["data"]["items"]
    }
    assert types["core.custom"]["available"] and types["core.custom"]["source"] == "custom"
    assert types[identity]["available"] and types[identity]["page_count"] == 0
    assert not any(
        row["type_id"] == identity
        for row in ctx.root.get("/v1/admin/pages").json()["data"]["items"]
    )
    assert ctx.client().get("/v1/admin/pages/types").status_code == 401

    identifier = str(uuid4())
    request: dict[str, Any] = {
        "id": identifier,
        "type_id": identity,
        "expected_revision": 0,
        "operation_id": str(uuid4()),
        "apply": True,
        "value": {
            "title": ctx.name + " code A",
            "path": "/" + ctx.name + "-one",
            "enabled": True,
            "options": {
                "people": [{"name": "Test person", "extra": {"score": 3.5}}],
                "unknown": {"nested": [False, 0, None, {"路径": "/any", "query": "unchanged"}]},
            },
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
    assert (
        ctx.root.post(
            "/v1/admin/pages/save",
            json={
                **request,
                "operation_id": str(uuid4()),
                "value": {**request["value"], "javascript_position": "head"},
            },
            headers=headers(ctx.root),
        ).status_code
        == 403
    )
    result = ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root))
    assert result.status_code == 200, result.text
    item = result.json()["data"]
    assert item["type_id"] == identity and item["source"] == "registered"
    expected = {**manifest["pages"][0]["defaults"], **request["value"]["options"]}
    assert item["value"]["options"] == expected
    assert (
        ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root)).json()
        == result.json()
    )
    manifest["pages"][0]["seo_editable"] = False
    asyncio.run(install())
    rejected_seo = ctx.root.post(
        "/v1/admin/pages/save",
        headers=headers(ctx.root),
        json={
            **request,
            "operation_id": str(uuid4()),
            "expected_revision": 1,
            "value": {**request["value"], "seo_title": "Unauthorized override"},
        },
    )
    assert rejected_seo.status_code == 403, rejected_seo.text
    assert (
        ctx.root.get(f"/v1/admin/pages/{identifier}").json()["data"]["registration"]["seo_editable"]
        is False
    )
    second_id = str(uuid4())
    second = {
        **request,
        "id": second_id,
        "operation_id": str(uuid4()),
        "value": {
            **request["value"],
            "title": ctx.name + " code B",
            "path": "/" + ctx.name + "-two",
            "options": {"people": [{"name": "Other person"}]},
        },
    }
    response = ctx.root.post("/v1/admin/pages/save", json=second, headers=headers(ctx.root))
    assert response.status_code == 200, response.text

    async def restore_legacy_snapshot_format() -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                row = await session.get(ManagedPage, identifier)
                assert row is not None and row.published is not None
                row.applied_snapshot = dict(row.published)
                await session.commit()
        finally:
            await db.close()

    asyncio.run(restore_legacy_snapshot_format())
    asyncio.run(install())
    catalog = ctx.root.get("/v1/admin/pages")
    assert catalog.status_code == 200, catalog.text
    directory = catalog.json()["data"]["items"]
    assert {row["id"] for row in directory if row["type_id"] == identity} == {identifier, second_id}
    page_type = next(
        row
        for row in ctx.root.get("/v1/admin/pages/types").json()["data"]["items"]
        if row["id"] == identity
    )
    assert page_type["page_count"] == 2
    by_id = {page["id"]: page for page in published(ctx)}
    assert by_id[identifier]["options"] == expected
    assert by_id[second_id]["options"]["people"] == [{"name": "Other person"}]
    assert by_id[identifier]["path"] != by_id[second_id]["path"]
    change_type = {
        **request,
        "type_id": "core.custom",
        "expected_revision": 1,
        "operation_id": str(uuid4()),
    }
    rejected = ctx.root.post("/v1/admin/pages/save", json=change_type, headers=headers(ctx.root))
    assert rejected.status_code == 422 and rejected.json()["code"] == 42294
    unknown = {
        **request,
        "id": str(uuid4()),
        "type_id": ctx.name + ".missing",
        "operation_id": str(uuid4()),
    }
    rejected = ctx.root.post("/v1/admin/pages/save", json=unknown, headers=headers(ctx.root))
    assert rejected.status_code == 422 and rejected.json()["code"] == 42294
    removed = ctx.root.post(
        "/v1/admin/pages/remove",
        json={"id": identifier, "expected_revision": 1, "operation_id": str(uuid4())},
        headers=headers(ctx.root),
    )
    assert removed.status_code == 200 and removed.json()["data"]["archived"]
    assert identifier not in {page["id"] for page in published(ctx)}
    restored = ctx.root.post(
        "/v1/admin/pages/remove",
        json={
            "id": identifier,
            "expected_revision": 2,
            "operation_id": str(uuid4()),
            "restore": True,
        },
        headers=headers(ctx.root),
    )
    assert restored.status_code == 200 and not restored.json()["data"]["enabled"]
    assert restored.json()["data"]["value"]["options"] == expected
    assert second_id in {page["id"] for page in published(ctx)}


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

    template = {
        "id": str(uuid4()),
        "expected_revision": 0,
        "operation_id": str(uuid4()),
        "value": {"title": ctx.name + " shared", "html": "<p>Original source</p>"},
    }
    created = member.post("/v1/admin/pages/templates", json=template, headers=headers(member))
    assert created.status_code == 200, created.text
    child = {
        **body(ctx, path="/" + ctx.name + "-template-child", enabled=True),
        "type_id": created.json()["data"]["type_id"],
        "apply": True,
    }
    applied = ctx.root.post("/v1/admin/pages/save", json=child, headers=headers(ctx.root))
    assert applied.status_code == 200, applied.text
    update_template = {
        **template,
        "expected_revision": 1,
        "operation_id": str(uuid4()),
        "value": {**template["value"], "html": "<p>Changed source</p>"},
    }
    rejected = member.post(
        "/v1/admin/pages/templates", json=update_template, headers=headers(member)
    )
    assert rejected.status_code == 403 and rejected.json()["code"] == 40399
    still_live = next(page for page in published(ctx) if page["id"] == child["id"])
    assert still_live["html"] == template["value"]["html"]
    assert (
        ctx.root.get("/v1/admin/pages/templates/" + template["id"]).json()["data"]["revision"] == 1
    )


def test_page_types_ignore_retired_nodes_and_do_not_reserve_suggested_paths(
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

    async def install() -> None:
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
        finally:
            await db.close()

    asyncio.run(install())
    type_id = manifest.pages[0].id
    row = next(
        item
        for item in ctx.root.get("/v1/admin/pages/types").json()["data"]["items"]
        if item["id"] == type_id
    )
    assert row["available"] and row["page_count"] == 0
    assert row["registration"]["version"] == "1.0.0"
    request = {**body(ctx, html=""), "type_id": type_id}
    conflict = ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root))
    assert conflict.status_code == 409 and conflict.json()["code"] == 40997
    request["value"]["path"] += "-another"
    assert (
        ctx.root.post("/v1/admin/pages/save", json=request, headers=headers(ctx.root)).status_code
        == 200
    )


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


def test_template_is_a_type_and_updates_live_children_without_publishing_their_drafts(
    pages: AccountsTest,
) -> None:
    ctx = pages
    template_id = uuid4()
    definition = {
        "id": str(template_id),
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "value": {"title": ctx.name + " 模板", "html": "<h2>Shared version one</h2>"},
    }

    def post(path: str, request: dict[str, Any], status: int = 200) -> dict[str, Any]:
        result = ctx.root.post("/v1/admin/pages" + path, json=request, headers=headers(ctx.root))
        assert result.status_code == status, result.text
        return dict(result.json())

    assert ctx.client().get(f"/v1/admin/pages/templates/{template_id}").status_code == 401
    assert ctx.root.post("/v1/admin/pages/templates", json=definition).status_code == 403
    saved = post("/templates", definition)["data"]
    assert saved["revision"] == 1 and "path" not in saved["value"]
    assert saved == post("/templates", definition)["data"]
    types = ctx.root.get("/v1/admin/pages/types").json()["data"]["items"]
    template_type = next(item for item in types if item.get("template_id") == str(template_id))
    assert template_type["source"] == "template" and template_type["page_count"] == 0
    assert all(page["id"] != str(template_id) for page in published(ctx))

    first, second, draft_only = [
        {
            **body(ctx, path=f"/{ctx.name}-{suffix}", enabled=enabled, comments_open=False),
            "type_id": template_type["id"],
            "apply": enabled,
        }
        for suffix, enabled in [("one", True), ("two", True), ("draft", False)]
    ]
    children = [post("/save", payload)["data"] for payload in (first, second, draft_only)]
    assert all(page["value"]["html"] == definition["value"]["html"] for page in children)
    assert all(page["type_id"] == template_type["id"] for page in children)
    assert not children[2]["enabled"]
    directory = ctx.root.get("/v1/admin/pages").json()["data"]["items"]
    assert all(
        row["type_title"] == definition["value"]["title"]
        for row in directory
        if row["id"] in {item["id"] for item in children}
    )
    types = ctx.root.get("/v1/admin/pages/types").json()["data"]["items"]
    assert next(item for item in types if item["id"] == template_type["id"])["page_count"] == 3
    pending = {
        **first,
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "apply": False,
        "value": {**first["value"], "title": ctx.name + " pending", "comments_open": True},
    }
    assert post("/save", pending)["data"]["has_unapplied_changes"]
    update_template = {
        **definition,
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "value": {
            **definition["value"],
            "html": "<h2>Shared version two</h2>",
            "css": "h2 { margin: 0; }",
        },
    }
    updated = post("/templates", update_template)["data"]
    assert updated["revision"] == 2
    assert post("/templates", update_template)["data"] == updated
    assert (
        post("/templates", {**update_template, "operation_id": str(uuid4())}, 409)["code"] == 40992
    )
    public = {row["id"]: row for row in published(ctx)}
    for child in children[:2]:
        live = public[child["id"]]
        assert live["path"] == child["value"]["path"] and live["title"] == ctx.name
        assert not live["comments_open"]
        assert live["html"] == updated["value"]["html"] and live["css"] == updated["value"]["css"]
        assert live["revision"] > child["revision"] and "template" not in live
    assert children[2]["id"] not in public
    private = ctx.root.get(f"/v1/admin/pages/{first['id']}").json()["data"]
    assert private["value"]["title"] == pending["value"]["title"]
    assert private["value"]["comments_open"] and private["has_unapplied_changes"]
    assert private["applied_revision"] == 1 and private["template"]["revision"] == 2
    enable = {
        "id": draft_only["id"],
        "operation_id": str(uuid4()),
        "expected_revision": children[2]["revision"],
        "enabled": True,
        "apply_draft": True,
    }
    assert post("/enabled", enable)["data"]["value"]["html"] == updated["value"]["html"]


def test_page_paths_default_from_title_and_reject_invalid_explicit_values(
    pages: AccountsTest,
) -> None:
    ctx = pages
    first = body(ctx)
    first["value"].pop("path")
    saved = ctx.root.post("/v1/admin/pages/save", json=first, headers=headers(ctx.root))
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["value"]["path"] == f"/{ctx.name}.html"
    second = {**first, "id": str(uuid4()), "operation_id": str(uuid4())}
    saved = ctx.root.post("/v1/admin/pages/save", json=second, headers=headers(ctx.root))
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["value"]["path"] == f"/{ctx.name}-2.html"
    for path, code in [
        ("hello", 42205),
        ("", 42205),
        ("/a?query=1", 42206),
        ("/%ff", 42206),
        ("/admin/test", 42207),
        ("/" + "a" * 500, 42208),
    ]:
        response = ctx.root.post(
            "/v1/admin/pages/save", json=body(ctx, path=path), headers=headers(ctx.root)
        )
        assert response.status_code == 422 and response.json()["code"] == code, response.text


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

    async def install() -> None:
        db = Database(config.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                for node in config.frontends:
                    await install_manifest(session, config, node, manifest)
                changed = manifest.model_copy(deep=True)
                changed.pages[0].version = "2.0.0"
                await install_manifest(session, config, "required-secondary", changed)
        finally:
            await db.close()

    asyncio.run(install())
    with TestClient(create_app(config)) as client:
        signed_in(client, ctx.name)
        type_id = manifest.pages[0].id
        row = next(
            row
            for row in client.get("/v1/admin/pages/types").json()["data"]["items"]
            if row["id"] == type_id
        )
        assert not row["available"]
        payload = {
            **body(ctx, html="", enabled=True),
            "type_id": type_id,
            "apply": True,
        }
        rejected = client.post("/v1/admin/pages/save", json=payload, headers=headers(client))
        assert rejected.status_code == 409 and rejected.json()["code"] == 40998
        draft = client.post(
            "/v1/admin/pages/save",
            json={**payload, "operation_id": str(uuid4()), "apply": False},
            headers=headers(client),
        )
        assert draft.status_code == 200 and not draft.json()["data"]["enabled"]


def test_page_version_restore_keeps_public_snapshot_and_records_source(pages: AccountsTest) -> None:
    prefix = "/v1/admin/pages"
    page = body(pages, html="<p>First draft</p>", seo_title="SEO title", hide_title=True)
    identity = page["id"]
    url = f"{prefix}/{identity}/versions"

    def post(path: str, payload: dict[str, Any], status: int = 200) -> dict[str, Any]:
        response = pages.root.post(path, json=payload, headers=headers(pages.root))
        assert response.status_code == status, response.text
        return dict(response.json())

    post(f"{prefix}/save", page)
    applied = post(
        f"{prefix}/save",
        {
            **page,
            "expected_revision": 1,
            "operation_id": str(uuid4()),
            "apply": True,
            "value": {
                **page["value"],
                "html": "<p>Public now</p>",
                "enabled": True,
                "hide_title": False,
            },
        },
    )["data"]
    versions = pages.root.get(url).json()["data"]
    assert [item["revision"] for item in versions["items"]] == [2, 1]
    assert [
        item["revision"]
        for item in pages.root.get(url, params={"before": 2}).json()["data"]["items"]
    ] == [1]
    assert pages.client().get(url + "/1").status_code == 401
    assert pages.root.get(url + "/99").status_code == 404
    assert pages.root.get(url + "/1").json()["data"]["value"]["hide_title"]
    before = next(item for item in published(pages) if item["id"] == identity)
    assert not before.get("hide_title")
    restore = {
        "revision": 1,
        "expected_revision": applied["revision"],
        "operation_id": str(uuid4()),
    }
    assert pages.root.post(url + "/restore", json=restore).status_code == 403
    result = post(url + "/restore", restore)["data"]
    assert result["value"]["html"] == "<p>First draft</p>"
    assert result["value"]["hide_title"] and result["value"]["enabled"]
    assert result["applied_revision"] == 2 and result["has_unapplied_changes"]
    assert post(url + "/restore", restore)["data"] == result
    assert post(url + "/restore", {**restore, "operation_id": str(uuid4())}, 409)["code"] == 40996
    assert next(item for item in published(pages) if item["id"] == identity) == before
    latest = pages.root.get(url + "/3").json()["data"]["version"]
    assert latest["action"] == "restore_draft" and latest["from_revision"] == 1
    post(
        f"{prefix}/save",
        {
            **page,
            "expected_revision": 3,
            "operation_id": str(uuid4()),
            "apply": True,
            "value": result["value"],
        },
    )
    live = next(item for item in published(pages) if item["id"] == identity)
    assert live["hide_title"] and live["seo_title"] == "SEO title"


def test_template_restore_requires_fresh_confirmation_before_children_change(
    pages: AccountsTest,
) -> None:
    prefix = "/v1/admin/pages"
    identity = uuid4()
    url = f"{prefix}/templates/{identity}"

    def post(path: str, payload: dict[str, Any], status: int = 200) -> dict[str, Any]:
        response = pages.root.post(path, json=payload, headers=headers(pages.root))
        assert response.status_code == status, response.text
        return dict(response.json())

    old = {"title": f"{pages.name} template", "html": "<p>Original template</p>"}
    newer = {**old, "html": "<p>Current template</p>"}
    definition = {
        "id": str(identity),
        "expected_revision": 0,
        "operation_id": str(uuid4()),
        "value": old,
    }
    template = post(f"{prefix}/templates", definition)["data"]
    assert template["applied_revision"] == 1
    child = {
        **body(pages, path=f"/{pages.name}-first", enabled=True),
        "type_id": template["type_id"],
        "apply": True,
    }
    first = post(f"{prefix}/save", child)["data"]
    post(
        f"{prefix}/templates",
        {**definition, "expected_revision": 1, "operation_id": str(uuid4()), "value": newer},
    )
    child_draft = post(
        f"{prefix}/save",
        {
            **child,
            "expected_revision": 2,
            "operation_id": str(uuid4()),
            "apply": False,
            "value": {**child["value"], "description": "Child's unsubmitted change"},
        },
    )["data"]
    ready = pages.root.get(url + "/impact").json()["data"]
    assert [page["id"] for page in ready["pages"]] == [first["id"]]
    assert pages.root.get(url + "/versions/1").json()["data"]["value"] == template["value"]
    restored = post(
        url + "/versions/restore",
        {"revision": 1, "expected_revision": 2, "operation_id": str(uuid4())},
    )["data"]
    assert restored["value"]["html"] == old["html"]
    assert restored["applied_revision"] == 2 and restored["applied_value"]["html"] == newer["html"]
    assert pages.root.get(url + "/versions").json()["data"]["items"][0]["from_revision"] == 1
    assert (
        next(item for item in published(pages) if item["id"] == child["id"])["html"]
        == newer["html"]
    )
    assert (
        post(
            f"{prefix}/templates",
            {**definition, "expected_revision": restored["revision"], "operation_id": str(uuid4())},
            409,
        )["code"]
        == 40992
    )
    preview = pages.root.get(url + "/impact").json()["data"]
    second = post(
        f"{prefix}/save",
        {
            **body(pages, path=f"/{pages.name}-second"),
            "type_id": template["type_id"],
        },
    )["data"]
    assert second["value"]["html"] == newer["html"] and not second["enabled"]
    apply = {
        "expected_revision": restored["revision"],
        "operation_id": str(uuid4()),
        "digest": preview["digest"],
    }
    assert post(url + "/apply", apply, 409)["code"] == 40988
    preview = pages.root.get(url + "/impact").json()["data"]
    assert {page["id"]: page["published"] for page in preview["pages"]} == {
        first["id"]: True,
        second["id"]: False,
    }
    confirmed = {**apply, "operation_id": str(uuid4()), "digest": preview["digest"]}
    applied = post(url + "/apply", confirmed)["data"]
    assert applied["revision"] == applied["applied_revision"] == 4
    assert applied["applied_value"] is None
    assert post(url + "/apply", confirmed)["data"] == applied
    live = next(item for item in published(pages) if item["id"] == child["id"])
    assert live["html"] == old["html"] and not live["description"]
    loaded = pages.root.get(f"{prefix}/{first['id']}").json()["data"]
    assert loaded["value"]["description"] == child_draft["value"]["description"]
    assert loaded["has_unapplied_changes"]


def test_protected_shortcodes_are_registered_and_only_unlocked_by_backend(
    pages: AccountsTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    from app.modules.content.models import Comment

    monkeypatch.setenv(pages.settings.auth.application_master_key_env, uuid4().hex * 2)
    catalog = pages.root.get("/v1/admin/shortcodes")
    assert catalog.status_code == 200, catalog.text
    native = {
        item["source"]["name"]: item
        for item in catalog.json()["data"]["items"]
        if item["id"] in {"core.reply-only", "core.password-area"}
    }
    assert set(native) == {"reply-only", "password-area"}
    assert all(item["origin"] == "backend" and not item["editable"] for item in native.values())
    password = uuid4().hex

    def save(path: str, payload: dict[str, Any]) -> Any:
        return pages.root.post(path, json=payload, headers=headers(pages.root))

    credential = save("/v1/admin/shortcodes/region-password", {"password": password})
    assert credential.status_code == 200, credential.text
    reference = credential.json()["data"]["credential"]
    reply_secret, password_secret = uuid4().hex, uuid4().hex
    payload = body(pages, enabled=True, html="", captcha_read="off")
    payload["apply"] = True
    payload["value"].update(
        edit_mode="components",
        component_document={
            "version": 1,
            "nodes": [
                {
                    "id": "reply",
                    "type": "core.markdown",
                    "props": {
                        "source": "{{< reply-only >}}" + reply_secret + "{{< /reply-only >}}"
                    },
                },
                {
                    "id": "locked",
                    "type": "core.html",
                    "props": {
                        "source": '{{< password-area credential="'
                        + reference
                        + '" >}}'
                        + password_secret
                        + "{{< /password-area >}}"
                    },
                },
            ],
        },
    )
    rejected = save(
        "/v1/admin/pages/save",
        {
            **payload,
            "operation_id": str(uuid4()),
            "value": {
                **payload["value"],
                "description": "{{<\t reply-only >}}secret{{< /reply-only >}}",
            },
        },
    )
    assert rejected.status_code == 422 and rejected.json()["code"] == 42284
    applied = save("/v1/admin/pages/save", payload)
    assert applied.status_code == 200, applied.text
    live = next(page for page in published(pages) if page["id"] == payload["id"])
    for secret in (password, reference, password_secret, reply_secret):
        assert secret not in str(live)
    nodes = live["component_document"]["nodes"]
    reply_id = nodes[0]["props"]["source"].split('"')[1]
    password_id = nodes[1]["props"]["source"].split('"')[1]
    anonymous = pages.client()
    url = "/v1/reader/shortcode-region"
    denied = anonymous.post(url, json={"id": reply_id}, headers=headers(anonymous))
    assert denied.status_code == 401, denied.text
    denied = save(url, {"id": reply_id})
    assert denied.status_code == 403 and denied.json()["code"] == 40382, denied.text
    wrong = anonymous.post(
        url, json={"id": password_id, "password": "wrong"}, headers=headers(anonymous)
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == 40383, wrong.text
    unlocked = anonymous.post(
        url, json={"id": password_id, "password": password}, headers=headers(anonymous)
    )
    assert unlocked.status_code == 200 and unlocked.json()["data"]["source"] == password_secret
    assert unlocked.headers["Cache-Control"] == "private, no-store"

    async def moderate(status: str) -> None:
        database = Database(pages.settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                region = await session.get(ShortcodeRegion, UUID(password_id))
                assert region is not None and password_secret not in region.body
                actor = await session.scalar(select(User).where(User.username == pages.name))
                assert actor is not None
                comment = await session.scalar(
                    select(Comment).where(
                        Comment.page_id == UUID(payload["id"]), Comment.author_user_id == actor.id
                    )
                )
                if comment is None:
                    session.add(
                        Comment(
                            page_id=UUID(payload["id"]),
                            author_user_id=actor.id,
                            author=pages.name,
                            content="Test reply",
                            status=status,
                            created_at=datetime.now(UTC),
                        )
                    )
                else:
                    comment.status = status
                await session.commit()
        finally:
            await database.close()

    asyncio.run(moderate("pending"))
    assert save(url, {"id": reply_id}).status_code == 403
    asyncio.run(moderate("approved"))
    reply = save(url, {"id": reply_id})
    assert reply.status_code == 200 and reply.json()["data"]["source"] == reply_secret
    assert reply.headers["Cache-Control"] == "private, no-store"
