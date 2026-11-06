"""Authored page language, retained-source toggles and safe navigation resource boundaries."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from tests.test_accounts import AccountsTest, headers
from tests.test_accounts import accounts as accounts_fixture
from tests.test_managed_pages import body, published
from tests.test_managed_pages import pages as pages_fixture

pages = pages_fixture
accounts = accounts_fixture


def post(ctx: AccountsTest, path: str, value: dict[str, Any], status: int = 200) -> dict[str, Any]:
    response = ctx.root.post("/v1/admin/pages/" + path, json=value, headers=headers(ctx.root))
    assert response.status_code == status, response.text
    result = response.json()["data"] if status == 200 else response.json()
    assert isinstance(result, dict)
    return result


def test_directory_toggle_retains_applied_source_and_private_draft(pages: AccountsTest) -> None:
    ctx = pages
    request = body(ctx, enabled=True)
    saved = post(ctx, "save", request)
    toggle = {
        "id": saved["id"],
        "expected_revision": saved["revision"],
        "enabled": True,
        "operation_id": str(uuid4()),
    }
    post(ctx, "enabled", toggle, 409)
    enabled_request = {**toggle, "operation_id": str(uuid4()), "apply_draft": True}
    active = post(ctx, "enabled", enabled_request)
    assert active["has_applied_snapshot"] and not active["has_unapplied_changes"]
    assert post(ctx, "enabled", enabled_request) == active
    draft = post(
        ctx,
        "save",
        {
            **request,
            "expected_revision": active["revision"],
            "operation_id": str(uuid4()),
            "value": {
                **request["value"],
                "path": request["value"]["path"] + "-next",
                "html": "<p>PRIVATE LATER SOURCE</p>",
            },
        },
    )
    assert draft["has_unapplied_changes"]
    off = post(
        ctx,
        "enabled",
        {
            **toggle,
            "enabled": False,
            "expected_revision": draft["revision"],
            "operation_id": str(uuid4()),
        },
    )
    assert all(p["id"] != saved["id"] for p in published(ctx))
    on = post(
        ctx,
        "enabled",
        {**toggle, "expected_revision": off["revision"], "operation_id": str(uuid4())},
    )
    public = next(p for p in published(ctx) if p["id"] == saved["id"])
    assert public["path"] == request["value"]["path"] and public["html"] == request["value"]["html"]
    assert on["value"]["html"] == "<p>PRIVATE LATER SOURCE</p>" and on["has_unapplied_changes"]
    post(ctx, "enabled", {**toggle, "operation_id": str(uuid4())}, 409)


def test_page_languages_group_list_independent_publish_and_original_removal(
    pages: AccountsTest,
) -> None:
    ctx = pages
    original = post(ctx, "save", {**body(ctx, enabled=True), "apply": True})
    group = original["translations"]
    request = {
        "id": original["id"],
        "expected_revision": group["revision"],
        "operation_id": str(uuid4()),
        "language": "en",
        "copy_source": True,
    }
    translated = post(ctx, "translations/create", request)
    assert post(ctx, "translations/create", request) == translated
    member = next(m for m in translated["members"] if m["language"] == "en")
    english = ctx.root.get("/v1/admin/pages/" + member["id"]).json()["data"]
    assert not english["value"]["enabled"] and not english["value"]["javascript_enabled"]
    assert english["value"]["path"].startswith("/en/")
    assert all(p["id"] != english["id"] for p in published(ctx))
    directory = ctx.root.get("/v1/admin/pages").json()["data"]["items"]
    assert original["id"] in {r["id"] for r in directory} and english["id"] not in {
        r["id"] for r in directory
    }
    post(
        ctx,
        "translations/create",
        {**request, "expected_revision": translated["revision"], "operation_id": str(uuid4())},
        409,
    )
    post(
        ctx,
        "translations/create",
        {
            **request,
            "language": "fr",
            "expected_revision": translated["revision"],
            "operation_id": str(uuid4()),
        },
        422,
    )
    active = post(
        ctx,
        "save",
        {
            "id": english["id"],
            "expected_revision": english["revision"],
            "value": {**english["value"], "enabled": True},
            "apply": True,
            "operation_id": str(uuid4()),
        },
    )
    public = [r for r in published(ctx) if r["translation_group_id"] == translated["id"]]
    assert len(public) == 2 and {p["language"] for p in public} == {"zh-CN", "en"}
    assert next(p for p in public if p["id"] == original["id"])["is_original"]
    updated = post(
        ctx,
        "save",
        {
            "id": original["id"],
            "expected_revision": original["revision"],
            "value": {**original["value"], "html": "<h2>Updated original</h2>"},
            "apply": True,
            "operation_id": str(uuid4()),
        },
    )
    catalog = post(ctx, "translations/query", {"id": english["id"]})
    assert next(m for m in catalog["members"] if m["id"] == english["id"])["needs_review"]
    catalog = post(
        ctx,
        "translations/review",
        {
            "id": english["id"],
            "expected_revision": catalog["revision"],
            "operation_id": str(uuid4()),
        },
    )
    assert not next(m for m in catalog["members"] if m["id"] == english["id"])["needs_review"]
    post(
        ctx,
        "remove",
        {
            "id": original["id"],
            "expected_revision": updated["revision"],
            "operation_id": str(uuid4()),
        },
    )
    directory = ctx.root.get("/v1/admin/pages").json()["data"]["items"]
    anchor = next(r for r in directory if r["id"] == english["id"])
    assert anchor["enabled"] and anchor["is_original"]
    assert next(p for p in published(ctx) if p["id"] == active["id"])["is_original"]
    post(
        ctx,
        "remove",
        {"id": active["id"], "expected_revision": active["revision"], "operation_id": str(uuid4())},
    )
    original_trash = ctx.root.get("/v1/admin/pages/" + original["id"]).json()["data"]
    restored = post(
        ctx,
        "remove",
        {
            "id": original["id"],
            "expected_revision": original_trash["revision"],
            "restore": True,
            "operation_id": str(uuid4()),
        },
    )
    assert restored["is_original"] and not restored["enabled"]
    assert original["id"] in {
        r["id"]
        for r in ctx.root.get("/v1/admin/pages").json()["data"]["items"]
        if not r["archived"]
    }


def test_page_language_link_unlink_preserves_addresses_and_rejects_self_language(
    pages: AccountsTest,
) -> None:
    ctx = pages
    original = post(ctx, "save", body(ctx))
    other_request = body(ctx, path="/" + ctx.name + "-en")
    other = post(ctx, "save", {**other_request, "language": "en"})
    linked = post(
        ctx,
        "translations/link",
        {
            "id": original["id"],
            "target_id": other["id"],
            "expected_revision": original["translations"]["revision"],
            "operation_id": str(uuid4()),
        },
    )
    detached = post(
        ctx,
        "translations/unlink",
        {"id": other["id"], "expected_revision": linked["revision"], "operation_id": str(uuid4())},
    )
    assert detached["source_id"] == other["id"] and len(detached["members"]) == 1
    assert detached["members"][0]["path"] == other_request["value"]["path"]
    assert (
        len(
            ctx.root.get("/v1/admin/pages/" + original["id"]).json()["data"]["translations"][
                "members"
            ]
        )
        == 1
    )


def test_navigation_queries_only_applied_titles_paths_and_tracks_renames(
    pages: AccountsTest,
) -> None:
    ctx = pages
    draft = post(ctx, "save", body(ctx, enabled=True))

    def choices(identity: str | None = None) -> dict[str, Any]:
        response = ctx.root.post(
            "/v1/admin/settings/navigation-resources",
            json={"kind": "page", "search": ctx.name, "id": identity},
            headers=headers(ctx.root),
        )
        assert response.status_code == 200, response.text
        assert "html" not in response.text and "options" not in response.text
        result = response.json()["data"]
        assert isinstance(result, dict)
        return result

    assert choices()["total"] == 0
    active = post(
        ctx,
        "save",
        {
            "id": draft["id"],
            "expected_revision": draft["revision"],
            "operation_id": str(uuid4()),
            "value": draft["value"],
            "apply": True,
        },
    )
    assert choices()["items"][0]["id"] == active["id"]
    assert choices(active["id"])["total"] == 1
    assert choices(str(uuid4()))["total"] == 0
    private = post(
        ctx,
        "save",
        {
            "id": active["id"],
            "expected_revision": active["revision"],
            "operation_id": str(uuid4()),
            "value": {**active["value"], "path": active["value"]["path"] + "-new"},
        },
    )
    assert choices()["items"][0]["path"] == active["value"]["path"]
    post(
        ctx,
        "save",
        {
            "id": private["id"],
            "expected_revision": private["revision"],
            "operation_id": str(uuid4()),
            "value": private["value"],
            "apply": True,
        },
    )
    assert choices()["items"][0]["path"].endswith("-new")


def test_bound_menu_persists_uuid_audit_and_follows_primary_public_path(
    pages: AccountsTest,
) -> None:
    ctx = pages
    original = ctx.root.get("/v1/admin/settings").json()["data"]
    active = post(ctx, "save", {**body(ctx, enabled=True), "apply": True})
    value = deepcopy(original["value"])
    value["site"]["navigation"] = [
        {
            "id": "bound-page-proof",
            "label": "Bound proof",
            "href": "/stale",
            "reference": {"kind": "page", "id": active["id"]},
        }
    ]
    try:
        response = ctx.root.patch(
            "/v1/admin/settings",
            json={"expected_revision": original["revision"], "value": value},
            headers=headers(ctx.root),
        )
        assert response.status_code == 200, response.text
        assert (
            response.json()["data"]["value"]["site"]["navigation"][0]["reference"]["id"]
            == active["id"]
        )
        public = ctx.root.get("/v1/site").json()["data"]
        assert public["navigation"][0]["href"] == active["value"]["path"]
        draft = post(
            ctx,
            "save",
            {
                "id": active["id"],
                "expected_revision": active["revision"],
                "operation_id": str(uuid4()),
                "value": {**active["value"], "path": active["value"]["path"] + "-changed"},
            },
        )
        assert (
            ctx.root.get("/v1/site").json()["data"]["navigation"][0]["href"]
            == active["value"]["path"]
        )
        applied = post(
            ctx,
            "save",
            {
                "id": active["id"],
                "expected_revision": draft["revision"],
                "operation_id": str(uuid4()),
                "value": draft["value"],
                "apply": True,
            },
        )
        assert ctx.root.get("/v1/site").json()["data"]["navigation"][0]["href"].endswith("-changed")
        post(
            ctx,
            "enabled",
            {
                "id": active["id"],
                "expected_revision": applied["revision"],
                "operation_id": str(uuid4()),
                "enabled": False,
            },
        )
        assert ctx.root.get("/v1/site").json()["data"]["navigation"] == []
    finally:
        state = ctx.root.get("/v1/admin/settings").json()["data"]
        restored = ctx.root.patch(
            "/v1/admin/settings",
            json={"expected_revision": state["revision"], "value": original["value"]},
            headers=headers(ctx.root),
        )
        assert restored.status_code == 200, restored.text
