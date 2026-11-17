"""Boundary checks for the administrator's public settings and node policy."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.content.schemas import SiteDTO
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.extensions_schemas import InstalledTheme
from app.modules.settings.schemas import (
    CachePolicy,
    CodePreferences,
    FooterLink,
    FooterPreferences,
    MenuItem,
    SitePreferences,
    ThemePreferences,
)
from tools.export_settings_contract import build_schema


def test_settings_contract_matches_published_artifact() -> None:
    assert (
        json.loads(Path("contracts/settings-v1.schema.json").read_text(encoding="utf-8"))
        == build_schema()
    )


def test_theme_recommendations_are_optional_and_canonical() -> None:
    assert InstalledTheme(id="argon", title="Argon").recommended_plugins is None
    assert (
        InstalledTheme(id="argon", title="Argon", recommended_plugins=[]).recommended_plugins == []
    )
    assert InstalledTheme(
        id="argon", title="Argon", recommended_plugins=["core.gallery", "core.book"]
    ).recommended_plugins == ["core.book", "core.gallery"]


@pytest.mark.parametrize(
    "plugins",
    [["book"], ["core.book", "core.book"], [f"core.plugin-{index}" for index in range(101)]],
)
def test_theme_recommendations_reject_invalid_lists(plugins: list[str]) -> None:
    with pytest.raises(ValidationError):
        InstalledTheme(id="argon", title="Argon", recommended_plugins=plugins)


def test_public_snapshot_site_contract_matches_public_dto() -> None:
    artifact = json.loads(Path("contracts/public-v0.1.schema.json").read_text(encoding="utf-8"))
    site = SiteDTO.model_json_schema(ref_template="#/definitions/{model}")
    definitions = site.pop("$defs", {})
    assert artifact["definitions"]["Site"] == site
    for name, value in definitions.items():
        assert artifact["definitions"][name] == value


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "//evil.test/x",
        "https://user:secret@example.com",
        "/\\evil",
        "https://example.com/\n",
    ],
)
def test_settings_reject_unsafe_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        MenuItem(id="nav", label="link", href=url)


def test_blank_navigation_is_authoritative_and_legacy_settings_survive() -> None:
    original = {
        "name": "Campfire",
        "author": "Author",
        "description": "Original",
        "notice": "",
        "navigation": [],
    }
    value = public_settings(original)
    value.site.description = "Changed"
    value.site.navigation = []
    stored = stored_settings(original, value)
    assert public_settings(stored) == value
    assert stored["navigation"] == []
    assert stored["description"] == "Changed"


def test_navigation_labels_are_independent_by_language_and_legacy_values_survive() -> None:
    menu = MenuItem(
        id="home",
        label="首页",
        labels={"zh-CN": "首页", "en": "Home"},
        href="/",
    )
    assert menu.labels == {"zh-CN": "首页", "en": "Home"}
    original = {
        "name": "Campfire",
        "author": "A",
        "description": "",
        "navigation": [menu.model_dump(mode="json")],
    }
    value = public_settings(original)
    assert value.site.navigation[0].labels["en"] == "Home"
    assert public_settings(stored_settings(original, value)) == value
    for invalid in (
        {"bad code": "Home"},
        {"en": "   "},
        {"en": "\n"},
        {"en": "x" * 61},
    ):
        with pytest.raises(ValidationError):
            MenuItem(id="home", label="首页", labels=invalid, href="/")


def test_navigation_is_owned_by_each_theme_and_legacy_site_navigation_remains_valid() -> None:
    site_menu = MenuItem(id="site-home", label="首页", href="/")
    theme_menu = MenuItem(id="theme-home", label="Home", href="/")
    original = {
        "name": "Campfire",
        "author": "A",
        "description": "",
        "navigation": [site_menu.model_dump(mode="json")],
        "theme_options": {
            "argon": ThemePreferences(navigation=[theme_menu]).model_dump(mode="json"),
            "paper": ThemePreferences(paper_tone="white").model_dump(mode="json"),
        },
        "extensions": {"theme": "argon"},
    }
    value = public_settings(original)
    assert value.site.navigation[0].id == "site-home"
    assert value.theme.navigation is not None
    assert value.theme.navigation[0].id == "theme-home"
    assert value.theme_options["paper"].navigation is None
    stored = stored_settings(original, value)
    assert stored["navigation"][0]["id"] == "site-home"
    assert stored["theme_options"]["argon"]["navigation"][0]["id"] == "theme-home"


def test_code_preferences_are_bounded_and_legacy_defaults_round_trip() -> None:
    original = {"name": "Campfire", "author": "A", "description": ""}
    value = public_settings(original)
    assert value.theme.code == CodePreferences()
    value.theme.code = CodePreferences(
        light_scheme="github", line_numbers=False, collapse_lines=24, font_size=14
    )
    assert public_settings(stored_settings(original, value)) == value
    for invalid in [
        {"light_scheme": "custom.css"},
        {"collapse_lines": -1},
        {"collapse_lines": 201},
        {"font_size": "14"},
        {"tab_size": 9},
        {"line_height": 0.2},
        {"font_media_id": "https://untrusted.test/font.woff2"},
        {"extra": True},
    ]:
        with pytest.raises(ValidationError):
            CodePreferences.model_validate(invalid)


def test_theme_options_are_isolated_per_theme_and_legacy_values_migrate() -> None:
    original = {
        "name": "Campfire",
        "author": "Author",
        "description": "",
        "hero_title": "Legacy hero",
        "theme": {"accent": "#123456"},
        "extensions": {"theme": "paper"},
    }
    value = public_settings(original)
    assert value.theme.accent == "#123456"
    assert value.theme_options["paper"].accent == "#123456"
    value.theme_options["paper"].paper_tone = "gray"
    value.theme_options["argon"] = value.theme_options["paper"].model_copy(
        update={"accent": "#abcdef", "paper_tone": "white"}
    )
    stored = stored_settings(original, value)
    restored = public_settings(stored)
    assert restored.theme.accent == "#123456"
    assert restored.theme.paper_tone == "gray"
    assert restored.theme_options["argon"].accent == "#abcdef"


def test_footer_links_and_glossary_settings_validate() -> None:
    footer = FooterPreferences(
        copyright="Copyright",
        links=[FooterLink(id="beian", label="备案", href="https://example.com")],
        html="<p>Footer</p>",
    )
    assert footer.links[0].href == "https://example.com"
    assert footer.model_dump()["html"] == "<p>Footer</p>"
    assert "html" not in FooterPreferences().model_dump()
    with pytest.raises(ValidationError):
        FooterPreferences(html="a" * 8001)
    with pytest.raises(ValidationError):
        FooterPreferences(
            links=[
                FooterLink(id="same", label="A", href="/a"),
                FooterLink(id="same", label="B", href="/b"),
            ]
        )


def test_site_font_scale_default_and_saved_value() -> None:
    assert ThemePreferences().font_scale_percent == 100
    assert "font_scale_percent" not in ThemePreferences().model_dump()
    preferences = ThemePreferences(font_scale_percent=125)
    assert preferences.model_dump()["font_scale_percent"] == 125
    for value in (74, 151, 90.5, "120"):
        with pytest.raises(ValidationError):
            ThemePreferences.model_validate({"font_scale_percent": value})


def test_duplicate_navigation_ids_and_missing_title_placeholder_rejected() -> None:
    with pytest.raises(ValidationError):
        SitePreferences(
            name="Site",
            author="A",
            description="",
            navigation=[
                MenuItem(id="same", label="A", href="/"),
                MenuItem(id="same", label="B", href="/b"),
            ],
        )
    with pytest.raises(ValidationError):
        SitePreferences(name="Site", author="A", description="", title_template="%site%")


def test_policy_is_bounded_and_cannot_expire_public_snapshots() -> None:
    for invalid in [
        {"snapshot_ttl": 60},
        {"request_timeout_seconds": 31},
        {"dynamic_freshness_seconds": 0},
        {"request_timeout_seconds": "8"},
    ]:
        with pytest.raises(ValidationError):
            CachePolicy.model_validate(invalid)


def test_scheduler_import_does_not_load_business_or_api() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.entrypoints.scheduler; "
            "assert 'app.modules.settings.delivery' not in sys.modules; "
            "assert 'app.entrypoints.api' not in sys.modules",
        ],
        cwd=root,
        env={**os.environ, "CONFIG_FILE": str(root / "config.example.toml")},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_notice_title_is_optional_and_round_trips_without_losing_legacy_content() -> None:
    original = {
        "name": "Campfire",
        "author": "Author",
        "description": "",
        "notice": "Existing notice",
    }
    value = public_settings(original)
    assert value.site.notice_title == "" and value.site.notice == "Existing notice"
    value.site.notice_title = "Announcement"
    stored = stored_settings(original, value)
    assert public_settings(stored).site.notice_title == "Announcement"
    assert stored["notice"] == "Existing notice"
    with pytest.raises(ValidationError):
        SitePreferences(name="Site", author="A", description="", notice_title="x" * 121)


def test_sidebar_profile_preserves_empty_links_and_validates_destinations() -> None:
    value = public_settings({"name": "Campfire", "author": "Yishang"})
    assert value.theme.sidebar_profile.personal_links is None
    value.theme.sidebar_profile.personal_links = []
    value.theme.hero_animation = "typewriter"
    stored = public_settings(stored_settings({}, value))
    assert stored.theme.sidebar_profile.personal_links == []
    assert stored.theme.hero_animation == "typewriter"

    for profile in (
        {"articles_url": "//example.test/path"},
        {"friend_links": [{"id": "friend", "label": "Friend", "href": "javascript:alert(1)"}]},
        {"personal_links": [{"id": "link", "label": " ", "href": "/about"}]},
    ):
        with pytest.raises(ValidationError):
            ThemePreferences.model_validate({"sidebar_profile": profile})


def test_sidebar_navigation_keeps_empty_values_separate_from_inherited_values() -> None:
    state = public_settings({"name": "Campfire", "author": "Yishang"})
    assert state.theme.sidebar_navigation.menu is None
    state.theme.sidebar_navigation.menu = []
    state.theme.sidebar_navigation.alias = ""
    restored = public_settings(stored_settings({}, state))
    assert restored.theme.sidebar_navigation.menu == []
    assert restored.theme.sidebar_navigation.alias == ""
    assert restored.theme.sidebar_navigation.title is None
    with pytest.raises(ValidationError, match="duplicate menu identifiers"):
        ThemePreferences.model_validate(
            {
                "sidebar_navigation": {
                    "menu": [
                        {"id": "duplicate", "href": "/", "label": "Home"},
                        {"id": "duplicate", "href": "/about", "label": "About"},
                    ]
                }
            }
        )


def test_header_brand_preserves_legacy_logo_and_stores_mode_per_theme() -> None:
    state = public_settings(
        {
            "name": "Campfire",
            "hero_title": "Banner title",
            "theme": {"header_logo_url": "/mark.png"},
            "theme_options": {"paper": {"header_brand_text": "Paper"}},
        }
    )
    assert state.theme.header_brand_mode == "both"
    assert state.theme.header_brand_text == ""
    assert state.theme.header_logo_url == "/mark.png"
    state.theme.header_brand_mode = "text"
    state.theme.header_brand_text = "Top bar"
    restored = public_settings(stored_settings({}, state))
    assert restored.theme.header_brand_mode == "text"
    assert restored.theme.header_brand_text == "Top bar"
    assert restored.theme.header_logo_url == "/mark.png"
    assert restored.theme.hero_title == "Banner title"
    assert restored.theme_options["paper"].header_brand_text == "Paper"

    for invalid in ({"header_brand_mode": "hidden"}, {"header_brand_text": "x" * 161}):
        with pytest.raises(ValidationError):
            ThemePreferences.model_validate(invalid)
