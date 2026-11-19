"""Small, synchronous, process-local hooks for committed business outcomes."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

BusinessAction = Literal[
    "login",
    "comment_submit",
    "comment_moderate",
    "publish",
    "withdraw",
    "schedule",
    "schedule_cancel",
    "page_visibility",
    "registration",
    "email_verification",
    "password_recovery",
    "password_change",
    "admin_password_reset",
    "account_access",
    "notification",
]
BusinessResult = Literal["success", "failure", "challenge", "suppressed", "unknown"]


@dataclass(frozen=True)
class BusinessEvent:
    action: BusinessAction
    result: BusinessResult
    stage: str = ""
    request_id: str | None = None
    status: int | None = None
    error_code: int | None = None
    reason: str = ""
    resource_kind: str = ""
    resource_id: UUID | None = None
    state: str = ""
    channel: str = ""
    notification_type: str = ""
    attempt: int | None = None
    affected_count: int | None = None
    role_changed: bool | None = None
    account_enabled: bool | None = None


BusinessHandler = Callable[[BusinessEvent], None]
BusinessSubscribers = tuple[tuple[str, BusinessHandler], ...]
logger = logging.getLogger(__name__)


def subscribers(data: dict[str, Any]) -> BusinessSubscribers:
    from app.core.plugins.loader import plugin_catalog
    from app.core.plugins.state import enabled_ids, installed_info

    try:
        enabled = enabled_ids(data)
        deployed = {item.id: item.build for item in installed_info(data)}
    except (KeyError, TypeError, ValueError):
        return ()
    catalog = plugin_catalog()
    active: set[str] = set()
    result = []
    for item in catalog.plugins:
        key = item.info.id
        if (
            key in enabled
            and deployed.get(key) == item.info.build
            and set(item.info.requires) <= active
        ):
            active.add(key)
            if item.business_handler is not None:
                result.append((key, item.business_handler))
    return tuple(result)


def dispatch(handlers: BusinessSubscribers, event: BusinessEvent) -> None:
    for plugin_id, handler in handlers:
        try:
            handler(event)
        except Exception as error:
            logger.warning("Business hook %s failed (%s)", plugin_id, type(error).__name__)
