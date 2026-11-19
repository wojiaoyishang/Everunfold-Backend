"""Stable entry point for trusted backend plugins. Only plugin.py is prescribed."""

from app.core.jobs.definitions import TaskDefinition
from app.core.jobs.execution import ResidentContext
from app.core.jobs.progress import task_progress
from app.core.plugins.business_events import BusinessEvent, BusinessRequest
from app.core.plugins.definition import Plugin, PluginRuntime, define_plugin
from app.core.plugins.registry import PluginRegistry
from app.core.plugins.route_runtime import PluginRequest, PluginRequestContext
from app.core.responses import JSONResponseFail, JSONResponseSuccess
from app.core.schemas import PublicModel, PublicResponse
from app.modules.captcha.providers import CaptchaProvider, verify_site_token
from app.modules.captcha.schemas import StoredProvider
from app.modules.notifications.definitions import NotificationEventDefinition
from app.modules.notifications.events import publish_event as publish_notification_event
from app.modules.notifications.platforms import (
    NotificationDeliveryRequest,
    NotificationDeliveryResult,
    NotificationPlatform,
)
from app.modules.settings.cache_event_schema import CacheEvent
from app.modules.shortcodes.schemas import ShortcodeParameter, ShortcodeSource

__all__ = [
    "PluginRequest",
    "PluginRequestContext",
    "JSONResponseSuccess",
    "JSONResponseFail",
    "PublicModel",
    "PublicResponse",
    "TaskDefinition",
    "task_progress",
    "ResidentContext",
    "NotificationDeliveryRequest",
    "NotificationDeliveryResult",
    "NotificationEventDefinition",
    "NotificationPlatform",
    "publish_notification_event",
    "CacheEvent",
    "BusinessEvent",
    "BusinessRequest",
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
