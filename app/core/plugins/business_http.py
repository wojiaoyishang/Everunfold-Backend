"""Associate committed events and HTTP rejections with the application's request ID."""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sqlalchemy import select
from starlette.types import Scope

from app.core.plugins.business_events import (
    BusinessAction,
    BusinessEvent,
    BusinessSubscribers,
    dispatch,
    subscribers,
)

if TYPE_CHECKING:
    from app.core.plugins.loader import PluginCatalog
    from app.core.runtime import Runtime

ENDPOINTS: dict[tuple[str, str], tuple[BusinessAction, str]] = {
    ("POST", "/v1/auth/login"): ("login", "password"),
    ("POST", "/v1/auth/login/second-factor"): ("login", "second_factor"),
    ("POST", "/v1/reader/comments"): ("comment_submit", ""),
    ("POST", "/v1/admin/comments/moderate"): ("comment_moderate", ""),
    ("POST", "/v1/admin/content/publish"): ("publish", "manual"),
    ("POST", "/v1/admin/content/components/publish"): ("publish", "manual"),
    ("POST", "/v1/admin/content/withdraw"): ("withdraw", "manual"),
    ("POST", "/v1/admin/content/schedule"): ("schedule", "manual"),
    ("POST", "/v1/admin/content/components/schedule"): ("schedule", "manual"),
    ("POST", "/v1/admin/content/cancel-schedule"): ("schedule_cancel", "manual"),
    ("POST", "/v1/admin/pages/enabled"): ("page_visibility", ""),
    ("POST", "/v1/auth/registration/confirm"): ("registration", "confirm"),
    ("POST", "/v1/account/email-verification/confirm"): ("email_verification", "confirm"),
    ("POST", "/v1/auth/password-recovery/confirm"): ("password_recovery", "confirm"),
    ("POST", "/v1/account/password"): ("password_change", ""),
}


@dataclass
class Operation:
    event: BusinessEvent
    committed: BusinessEvent | None = None
    error_code: int | None = None


current: ContextVar[Operation | None] = ContextVar("business_operation", default=None)


def mark_attempt(action: BusinessAction, *, stage: str = "", resource_kind: str = "") -> None:
    if operation := current.get():
        operation.event = replace(
            operation.event,
            action=action,
            stage=stage or operation.event.stage,
            resource_kind=resource_kind,
        )


def mark_committed(event: BusinessEvent) -> None:
    if operation := current.get():
        operation.committed = event


def mark_error_code(code: int) -> None:
    if operation := current.get():
        operation.error_code = code


class BusinessHTTP:
    def __init__(self, catalog: "PluginCatalog") -> None:
        self.has_handlers = any(item.business_handler is not None for item in catalog.plugins)
        self.handlers: BusinessSubscribers = ()

    async def _refresh(self, runtime: "Runtime") -> None:
        from app.modules.content.models import SiteSettings

        try:
            async with asyncio.timeout(2), runtime.database.session_factory() as session:
                data = await session.scalar(select(SiteSettings.data).where(SiteSettings.id == 1))
            self.handlers = subscribers(data or {})
        except Exception:
            self.handlers = ()

    async def _watch(self, runtime: "Runtime") -> None:
        while True:
            await asyncio.sleep(5)
            await self._refresh(runtime)

    @asynccontextmanager
    async def lifespan(self, runtime: "Runtime | None") -> AsyncIterator[None]:
        if runtime is None or not self.has_handlers:
            yield
            return
        await self._refresh(runtime)
        task = asyncio.create_task(self._watch(runtime))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.handlers = ()

    def begin(self, scope: Scope) -> Token[Operation | None] | None:
        if not self.handlers:
            return None
        path = scope["path"]
        root = scope.get("root_path", "")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        method = scope["method"]
        target = ENDPOINTS.get((method, path))
        if (
            target is None
            and re.fullmatch(r"/v1/admin/users/[^/]+/password", path)
            and method == "POST"
        ):
            target = "admin_password_reset", ""
        if target is None and re.fullmatch(r"/v1/admin/users/[^/]+", path) and method == "PATCH":
            target = "account_access", ""
        # A page save can be a draft. Let the service opt in only when applying it.
        if target is None and method == "POST" and path == "/v1/admin/pages/save":
            target = "page_visibility", "draft"
        if target is None:
            return None
        action, stage = target
        return current.set(Operation(BusinessEvent(action, "failure", stage=stage)))

    def finish(self, token: Token[Operation | None], request_id: str, status: int | None) -> None:
        operation = current.get()
        current.reset(token)
        if operation is None:
            return
        if operation.committed is not None:
            dispatch(
                self.handlers, replace(operation.committed, request_id=request_id, status=status)
            )
        elif status is not None and status >= 400 and operation.event.stage != "draft":
            dispatch(
                self.handlers,
                replace(
                    operation.event,
                    request_id=request_id,
                    status=status,
                    error_code=operation.error_code,
                ),
            )
