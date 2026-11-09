"""Stable entry point for trusted backend plugins. Only plugin.py is prescribed."""

from app.core.plugins.definition import Plugin, PluginRuntime, define_plugin
from app.core.plugins.registry import PluginRegistry
from app.modules.captcha.providers import CaptchaProvider, verify_site_token
from app.modules.shortcodes.schemas import ShortcodeParameter, ShortcodeSource

__all__ = [
    "CaptchaProvider",
    "Plugin",
    "PluginRegistry",
    "PluginRuntime",
    "ShortcodeParameter",
    "ShortcodeSource",
    "define_plugin",
    "verify_site_token",
]
