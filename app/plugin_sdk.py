"""Stable entry point for trusted backend plugins. Only plugin.py is prescribed."""

from app.core.jobs.definitions import TaskDefinition
from app.core.jobs.execution import ResidentContext
from app.core.jobs.progress import task_progress
from app.core.plugins.definition import Plugin, PluginRuntime, define_plugin
from app.core.plugins.registry import PluginRegistry
from app.modules.captcha.providers import CaptchaProvider, verify_site_token
from app.modules.captcha.schemas import StoredProvider
from app.modules.shortcodes.schemas import ShortcodeParameter, ShortcodeSource

__all__ = [
    "TaskDefinition",
    "task_progress",
    "ResidentContext",
    "CaptchaProvider",
    "Plugin",
    "PluginRegistry",
    "PluginRuntime",
    "ShortcodeParameter",
    "ShortcodeSource",
    "StoredProvider",
    "define_plugin",
    "verify_site_token",
]
