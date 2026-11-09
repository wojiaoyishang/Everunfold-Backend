"""Real extension boundaries: discovery, atomic registration, publication and cleanup."""

import asyncio
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.core.i18n import i18n
from app.core.plugins.definition import Plugin, PluginRuntime, define_plugin
from app.core.plugins.loader import collect_plugins, discover_plugins, plugin_catalog
from app.core.plugins.registry import PluginRegistry
from app.core.plugins.state import CATALOG_KEY, provider_state
from app.modules.settings.backend_plugins import validate_names
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.extensions_schemas import ExtensionInventory
from app.modules.shortcodes.schemas import ShortcodeSource


def test_single_entry_discovers_arbitrary_internal_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fixture_plugins"
    directory = root / "free_layout"
    directory.mkdir(parents=True)
    (directory / "my_helpers.py").write_text('TEXT = "Source from a freely named module"')
    (directory / "plugin.py").write_text("""
from app.plugin_sdk import define_plugin, ShortcodeSource
from .my_helpers import TEXT
def setup(registry):
    registry.shortcodes.register(ShortcodeSource(
        name="fixture:card", title="Card", html="<div>{{content}}</div>", example_body=TEXT))
plugin = define_plugin(id="fixture.card", title="Card", setup=setup)
""")
    # Helper directories without an entry are not executable plugins.
    (root / "ignored").mkdir()
    (root / "ignored" / "captcha.py").write_text('raise RuntimeError("must not import")')
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        result = discover_plugins(root, "fixture_plugins")
        assert len(result.plugins) == 1
        assert result.plugins[0].shortcodes[0].source.example_body.startswith("Source from")
        assert not list(root.rglob("manifest.*"))
    finally:
        for key in list(sys.modules):
            if key == "fixture_plugins" or key.startswith("fixture_plugins."):
                del sys.modules[key]


def test_class_and_function_registration_keeps_legacy_identifiers_and_seals_setup() -> None:
    loaded = plugin_catalog()
    codes = {c.source.name: c for p in loaded.plugins for c in p.shortcodes}
    for name in ("notice", "accordion"):
        assert codes[name].id == str(uuid5(NAMESPACE_URL, "everunfold:shortcode:" + name))
    assert codes["example:badge"].source.kind == "leaf"
    assert codes["example:card"].source.kind == "container"
    assert all(not code.editable for code in codes.values())
    retained: list[PluginRegistry] = []
    collect_plugins(
        [(define_plugin(id="test.empty", title="Empty", setup=retained.append), "0" * 64)]
    )
    with pytest.raises(RuntimeError, match="finished"):
        retained[0].locales.register("en", {"late": "no"})
    snapshot = loaded.snapshot()
    snapshot[0]["info"]["title"] = "mutated"
    assert loaded.snapshot()[0]["info"]["title"] != "mutated"
    assert i18n.t("example.cards.card", locale="en") == "Example card"


def test_duplicate_dependencies_and_invalid_setup_fail_atomically() -> None:
    def setup(registry: PluginRegistry) -> None:
        registry.shortcodes.register(ShortcodeSource(name="same", title="Same", html="{{content}}"))

    a = define_plugin(id="test.a", title="A", setup=setup)
    b = define_plugin(id="test.b", title="B", setup=setup)
    with pytest.raises(ValueError, match="Duplicate backend plugin"):
        collect_plugins([(a, "0" * 64), (a, "0" * 64)])
    with pytest.raises(ValueError, match="Duplicate backend shortcode"):
        collect_plugins([(a, "0" * 64), (b, "0" * 64)])
    a.requires, b.requires = ("test.b",), ("test.a",)
    with pytest.raises(ValueError, match="cyclic"):
        collect_plugins([(a, "0" * 64), (b, "0" * 64)])
    with pytest.raises(ValueError, match="Missing"):
        collect_plugins([(a, "0" * 64)])
    a.requires = ()
    b.setup = lambda registry: None  # type: ignore[method-assign]
    result = collect_plugins([(b, "0" * 64), (a, "0" * 64)])
    assert [p.info.id for p in result.plugins] == ["test.a", "test.b"]

    async def asynchronous(registry: PluginRegistry) -> None:
        pass

    a.setup = asynchronous  # type: ignore[method-assign,assignment]
    with pytest.raises(ValueError, match="synchronous"):
        collect_plugins([(a, "0" * 64)])


def test_lifecycle_cleans_up_partial_startup_in_reverse_dependency_order() -> None:
    calls: list[str] = []

    class Resource(Plugin):
        title = "Resource"

        async def startup(self, context: PluginRuntime) -> None:
            calls.append("start:" + self.id)
            if self.id == "test.b":
                raise RuntimeError("startup failed")

        async def shutdown(self, context: PluginRuntime) -> None:
            calls.append("stop:" + self.id)

    a, b = Resource(), Resource()
    a.id, b.id, b.requires = "test.a", "test.b", ("test.a",)
    catalog = collect_plugins([(b, "0" * 64), (a, "0" * 64)])

    async def run() -> None:
        async with catalog.lifespan(PluginRuntime(role="api", runtime=None)):
            pytest.fail("partially started plugins must not serve requests")

    with pytest.raises(RuntimeError, match="startup failed"):
        asyncio.run(run())
    assert calls == ["start:test.a", "start:test.b", "stop:test.b", "stop:test.a"]


def test_persisted_catalog_controls_projection_and_provider_activation() -> None:
    data: dict[str, Any] = {CATALOG_KEY: plugin_catalog().snapshot()}
    value = public_settings(data)
    assert {item.name for item in value.shortcodes} == {
        "notice",
        "accordion",
        "example:card",
        "example:badge",
    }
    assert CATALOG_KEY not in value.model_dump_json()
    assert public_settings(stored_settings(data, value)) == value
    assert stored_settings(data, value)["shortcodes"] == []
    data["extensions"] = {"disabled_plugins": ["example.cards", "core.captcha-google"]}
    assert {item.name for item in public_settings(data).shortcodes} == {"notice", "accordion"}
    assert not provider_state(data, "google.recaptcha-v2")[0]
    assert provider_state(data, "cloudflare.turnstile")[0]
    assert not provider_state({}, "cloudflare.turnstile")[0]
    assert provider_state({}, "local")[0]
    assert len(data[CATALOG_KEY]) == 5


def test_frontend_inventory_cannot_override_backend_or_custom_shortcodes() -> None:
    from app.modules.auth.service import AuthFailure

    for name in ("notice", "example:card", "custom:spoof"):
        manifest = ExtensionInventory.model_validate(
            {
                "plugins": [
                    {"id": "test.front", "title": "Test", "build": "0" * 64, "shortcodes": [name]}
                ],
                "pages": {"pages": []},
            }
        )
        with pytest.raises(AuthFailure):
            validate_names(plugin_catalog(), manifest)
