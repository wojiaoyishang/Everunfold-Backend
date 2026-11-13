"""Deployment catalogs never grant browser code-install privileges."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import FrontendConfig, InternalAuthConfig, Settings
from app.entrypoints.api import create_app
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.extensions_schemas import (
    ExtensionInventory,
    ExtensionPreferences,
    InstalledTheme,
)


def manifest() -> dict[str, Any]:
    return {
        "themes": [
            {"id": "argon", "title": "Argon"},
            {"id": "paper", "title": "Paper", "parent": "argon"},
        ],
        "plugins": [{"id": "core.people", "title": "People", "build": "a" * 64, "page_ids": []}],
        "pages": {"protocol": "pages-1", "pages": []},
    }


def test_activation_defaults_round_trip_and_invalid_identifiers() -> None:
    original = {"name": "Example", "author": "A"}
    value = public_settings(original)
    assert value.extensions.theme == "" and value.extensions.disabled_plugins == []
    value.extensions = ExtensionPreferences(theme="paper", disabled_plugins=["core.people"])
    assert public_settings(stored_settings(original, value)).extensions == value.extensions
    for body in [
        {"theme": "../../theme"},
        {"theme": "https://example.test/code"},
        {"disabled_plugins": ["core.people", "core.people"]},
        {"disabled_plugins": ["unknown/path"]},
    ]:
        with pytest.raises(ValidationError):
            ExtensionPreferences.model_validate(body)


def test_inventory_ownership_duplicate_and_parent_cycle_validation() -> None:
    value = manifest()
    ExtensionInventory.model_validate(value)
    changes: list[dict[str, Any]] = [
        {"themes": [{"id": "paper", "title": "Paper", "parent": "absent"}]},
        {"themes": [{"id": "paper", "title": "Paper", "parent": "paper"}]},
        {"plugins": [value["plugins"][0], value["plugins"][0]]},
        {
            "pages": {
                "protocol": "pages-1",
                "pages": [
                    {"id": "core.page", "version": "1.0.0", "title": "Page", "path": "/page"}
                ],
            }
        },
    ]
    for change in changes:
        with pytest.raises(ValidationError):
            ExtensionInventory.model_validate({**value, **change})


def test_theme_metadata_is_optional_bounded_and_keeps_developer_information() -> None:
    theme = InstalledTheme.model_validate(
        {
            "id": "paper",
            "title": "Paper",
            "version": "2.0.0",
            "parent": "argon",
            "description": "A minimal child theme",
            "preview": "/themes/paper.svg",
            "author": {"name": "A developer", "url": "https://example.test/author"},
            "homepage": "https://example.test/theme",
            "repository": "https://example.test/source",
            "license": "Custom license",
        }
    )
    assert InstalledTheme.model_validate(theme.model_dump()) == theme
    assert theme.author is not None and theme.author.name == "A developer"
    legacy = InstalledTheme(id="argon", title="Argon")
    assert legacy.author is None and legacy.preview == ""
    for change in ({"description": "x" * 501}, {"author": {"name": " "}}, {"license": "x" * 101}):
        with pytest.raises(ValidationError):
            InstalledTheme.model_validate({**theme.model_dump(), **change})


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "//example.test/image",
        "data:image/svg+xml,x",
        "https://name:secret@example.test",
        "https://example.test/\nimage",
        "/\\bad",
    ],
)
def test_theme_metadata_rejects_executable_or_credentialed_urls(url: str) -> None:
    for field in ("preview", "homepage", "repository"):
        with pytest.raises(ValidationError):
            InstalledTheme.model_validate({"id": "argon", "title": "Argon", field: url})
    with pytest.raises(ValidationError):
        InstalledTheme.model_validate(
            {"id": "argon", "title": "Argon", "author": {"name": "A", "url": url}}
        )


def test_internal_registration_requires_real_peer_and_token(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXTENSION_TEST_NODE_TOKEN", "local-test-node-token")
    configured = test_settings.model_copy(
        update={
            "internal_auth": InternalAuthConfig(allowed_peer_ips=["127.0.0.1"]),
            "frontends": {
                "test": FrontendConfig(
                    base_url="http://127.0.0.1:3000",
                    enabled=True,
                    nextjs_to_fastapi_token_env="EXTENSION_TEST_NODE_TOKEN",
                    fastapi_to_nextjs_token_env="EXTENSION_TEST_REVERSE_TOKEN",
                )
            },
        }
    )
    from app.modules.settings import extensions_router

    # The socket/token boundary test does not provision PostgreSQL.
    monkeypatch.setattr(
        extensions_router, "read_targets", AsyncMock(return_value=configured.frontends)
    )
    with TestClient(create_app(configured), client=("198.51.100.7", 4000)) as client:
        result = client.post(
            "/internal/extensions/test",
            json=manifest(),
            headers={
                "Authorization": "Bearer local-test-node-token",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        assert result.status_code == 403
    with TestClient(create_app(configured), client=("127.0.0.1", 4000)) as client:
        assert client.post("/internal/extensions/test", json=manifest()).status_code == 403
        assert (
            client.post(
                "/internal/extensions/unknown",
                json=manifest(),
                headers={"Authorization": "Bearer local-test-node-token"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/internal/extensions/test",
                json={},
                headers={"Authorization": "Bearer local-test-node-token"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/internal/extensions/test",
                content=b" " * (512 * 1024 + 1),
                headers={"Authorization": "Bearer local-test-node-token"},
            ).status_code
            == 413
        )
