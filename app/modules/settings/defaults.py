"""Upgrade legacy public values on read, without overwriting explicit empty preferences."""

from typing import Any

from app.modules.settings.schemas import PublicSettings, SitePreferences, ThemePreferences


def public_settings(data: dict[str, Any]) -> PublicSettings:
    menu = []
    for index, item in enumerate(data.get("navigation", [])):
        entry = {**item, "id": item.get("id") or f"menu-{index + 1}"}
        if not entry.get("icon"):
            entry["icon"] = "home" if entry.get("href") == "/" else "link"
        menu.append(entry)
    site = {key: data[key] for key in SitePreferences.model_fields if key in data}
    site.update(
        name=data.get("name") or "Everunfold",
        description=data.get("description", ""),
        author=data.get("author") or "Admin",
        avatar_url=data.get("avatar_url") or "",
        navigation=menu,
    )
    theme = {
        **data.get("theme", {}),
        "hero_title": data.get("hero_title", data.get("name", "Everunfold")),
        "hero_subtitle": data.get("hero_subtitle", ""),
        "background_url": data.get("hero_url") or "",
    }
    return PublicSettings(
        site=SitePreferences.model_validate(site),
        theme=ThemePreferences.model_validate(theme),
        shortcodes=data.get("shortcodes", []),
        extensions=data.get("extensions", {}),
    )


def stored_settings(original: dict[str, Any], value: PublicSettings) -> dict[str, Any]:
    theme = value.theme.model_dump(mode="json")
    return {
        **original,
        **value.site.model_dump(mode="json"),
        "hero_title": theme.pop("hero_title"),
        "hero_subtitle": theme.pop("hero_subtitle"),
        "hero_url": theme.pop("background_url") or None,
        "theme": theme,
        "extensions": value.extensions.model_dump(mode="json"),
        "shortcodes": [item.model_dump() for item in value.shortcodes],
    }
