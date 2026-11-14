"""Persisted plugin declarations drive publication; Python objects never enter a snapshot."""

from typing import Any

from app.modules.settings.extensions_schemas import BackendPluginInfo
from app.modules.shortcodes.registry import region_catalog
from app.modules.shortcodes.schemas import PublicShortcode, ShortcodeItem

CATALOG_KEY = "_backend_plugins"


def installed_info(data: dict[str, Any]) -> list[BackendPluginInfo]:
    return [BackendPluginInfo.model_validate(item["info"]) for item in data.get(CATALOG_KEY, [])]


def enabled_ids(data: dict[str, Any], disabled: list[str] | None = None) -> set[str]:
    disabled = (
        disabled if disabled is not None else data.get("extensions", {}).get("disabled_plugins", [])
    )
    entries = installed_info(data)
    enabled = {item.id for item in entries if item.id not in disabled}
    while True:
        allowed = {
            item.id for item in entries if item.id in enabled and set(item.requires) <= enabled
        }
        if allowed == enabled:
            return allowed
        enabled = allowed


def shortcode_items(data: dict[str, Any], disabled: list[str] | None = None) -> list[ShortcodeItem]:
    active = enabled_ids(data, disabled)
    return [
        *region_catalog(),
        *(
            ShortcodeItem.model_validate(code)
            for item in data.get(CATALOG_KEY, [])
            if item["info"]["id"] in active
            for code in item["shortcodes"]
        ),
    ]


def cache_event_subscribers(data: dict[str, Any]) -> list[str]:
    active = enabled_ids(data)
    return [
        item["info"]["id"]
        for item in data.get(CATALOG_KEY, [])
        if item.get("cache_events") == "cache-event-1" and item["info"]["id"] in active
    ]


def public_shortcodes(
    data: dict[str, Any], disabled: list[str] | None = None
) -> list[PublicShortcode]:
    return [item.compiled for item in shortcode_items(data, disabled)]


def provider_state(data: dict[str, Any], provider: str) -> tuple[bool, str]:
    if provider == "local":
        return True, "local"
    from app.core.plugins.loader import plugin_catalog

    catalog = plugin_catalog()
    owner = catalog.provider_owner(provider)
    local = next((item.info for item in catalog.plugins if item.info.id == owner), None)
    applied = next((item for item in installed_info(data) if item.id == owner), None)
    available = bool(
        local and applied and local.build == applied.build and owner in enabled_ids(data)
    )
    return available, f"{owner}:{applied.build if applied else 'unregistered'}:{available}"
