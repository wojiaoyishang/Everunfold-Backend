"""Read-only backend shortcode contributions, owned by their plugins."""

from app.modules.shortcodes.schemas import ShortcodeItem


def builtin_catalog() -> list[ShortcodeItem]:
    from app.core.plugins.loader import plugin_catalog

    return [
        code.model_copy(deep=True)
        for plugin in plugin_catalog().plugins
        for code in plugin.shortcodes
    ]
