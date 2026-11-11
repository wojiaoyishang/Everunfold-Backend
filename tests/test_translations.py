"""Sparse translation overrides remain separate from immutable program dictionaries."""

import pytest

from app.core.i18n import i18n
from tests.test_accounts import AccountsTest, headers
from tests.test_page_comments import comments as translation_database  # noqa: F401


def test_translation_overrides_feed_public_settings_and_reset_cleanly(
    request: pytest.FixtureRequest,
) -> None:
    ctx: AccountsTest = request.getfixturevalue("translation_database")
    request.addfinalizer(lambda: i18n.replace_overrides("backend", {}))
    base = "/v1/admin/settings/translations"
    state = ctx.root.get(base, headers=headers(ctx.root))
    assert state.status_code == 200, state.text
    initial = state.json()["data"]
    assert initial["revision"] == 1
    assert any(item["scope"] == "backend" for item in initial["backend_catalog"])
    backend_entry = next(item for item in initial["backend_catalog"] if item["key"] == "core.error")

    saved = ctx.root.patch(
        base,
        headers=headers(ctx.root),
        json={
            "expected_revision": 1,
            "changes": [
                {
                    "scope": "public",
                    "locale": "en",
                    "key": "搜索",
                    "source": "搜索",
                    "value": "Find",
                },
                {
                    "scope": "admin",
                    "locale": "en",
                    "key": "已选 {count} 项",
                    "source": "已选 {count} 项",
                    "value": "{count} selected items",
                },
                {
                    "scope": "backend",
                    "locale": "en",
                    "key": backend_entry["key"],
                    "source": backend_entry["source"],
                    "value": "Custom backend failure",
                },
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    value = saved.json()["data"]
    assert value["revision"] == 2
    assert len(value["overrides"]) == 3
    assert i18n.t("core.error", locale="en") == "Custom backend failure"

    settings = ctx.root.get("/v1/admin/settings", headers=headers(ctx.root))
    assert settings.status_code == 200, settings.text
    translations = settings.json()["data"]["value"]["translations"]
    assert translations["public"]["en"]["搜索"] == "Find"
    assert translations["admin"]["en"]["已选 {count} 项"] == "{count} selected items"
    assert translations["backend"]["en"]["core.error"] == "Custom backend failure"

    invalid = ctx.root.patch(
        base,
        headers=headers(ctx.root),
        json={
            "expected_revision": 2,
            "changes": [
                {
                    "scope": "public",
                    "locale": "en",
                    "key": "已选 {count} 项",
                    "source": "已选 {count} 项",
                    "value": "Selected {unknown}",
                }
            ],
        },
    )
    assert invalid.status_code == 422

    optional = ctx.root.patch(
        base,
        headers=headers(ctx.root),
        json={
            "expected_revision": 2,
            "changes": [
                {
                    "scope": "admin",
                    "locale": "en",
                    "key": "已选 {count} 项",
                    "source": "已选 {count} 项",
                    "value": "Selected",
                }
            ],
        },
    )
    assert optional.status_code == 200, optional.text
    assert optional.json()["data"]["revision"] == 3

    reset = ctx.root.patch(
        base,
        headers=headers(ctx.root),
        json={
            "expected_revision": 3,
            "changes": [
                {
                    "scope": "public",
                    "locale": "en",
                    "key": "搜索",
                    "source": "搜索",
                    "value": None,
                }
            ],
        },
    )
    assert reset.status_code == 200, reset.text
    remaining = reset.json()["data"]["overrides"]
    assert [(item["scope"], item["key"]) for item in remaining] == [
        ("admin", "已选 {count} 项"),
        ("backend", "core.error"),
    ]
