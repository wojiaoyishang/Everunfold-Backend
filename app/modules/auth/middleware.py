"""No-store authentication responses, strict browser origins and bounded login bodies."""

from collections.abc import Iterable

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.responses import JSONResponseFail


class BusinessCORSMiddleware:
    """Credentialed auth paths do not widen the existing public API's CORS behavior."""

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.plugins = CORSMiddleware(
            app,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Retry-After"],
        )
        self.private = CORSMiddleware(
            app,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=[
                "Accept",
                "Content-Type",
                "Authorization",
                "X-CSRF-Token",
                "X-Site-Locale",
                "X-Site-UI-Locale",
            ],
            expose_headers=["Retry-After"],
        )
        self.public = CORSMiddleware(
            app,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "PUT"],
            allow_headers=[
                "Accept",
                "Content-Type",
                "Authorization",
                "X-Everunfold-Visitor",
                "X-Site-Locale",
                "X-Site-UI-Locale",
                "X-Everunfold-Read-Receipt",
                "X-Everunfold-Read-Target",
            ],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target = (
            self.plugins
            if str(scope.get("path", "")).startswith("/v1/plugins/")
            else self.private
            if str(scope.get("path", "")).startswith(
                ("/v1/auth/", "/v1/admin/", "/v1/account/", "/v1/reader/")
            )
            else self.public
        )
        await target(scope, receive, send)


class AuthBoundaryMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] != "http" or not path.startswith(
            ("/v1/auth/", "/v1/admin/", "/v1/account/", "/v1/reader/", "/v1/plugins/")
        ):
            await self.app(scope, receive, send)
            return

        async def private_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "private, no-store"
                headers["Pragma"] = "no-cache"
                headers.append("Vary", "Cookie, Origin, Authorization")
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        write = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        machine = path == "/v1/auth/api-keys/introspect" or (
            (
                path.startswith(("/v1/admin/", "/v1/plugins/"))
                or path
                in {
                    "/v1/reader/access",
                    "/v1/reader/access-page",
                    "/v1/reader/comments",
                    "/v1/reader/captcha/challenge",
                }
            )
            and headers.get("authorization", "").lower().startswith("bearer efk_")
        )
        if path.startswith("/v1/plugins/"):
            from app.core.plugins.routes import public_plugin_request

            machine = machine or public_plugin_request(scope)
        if (origin is not None or (write and not machine)) and origin not in self.allowed_origins:
            await JSONResponseFail(status_code=403, code=40310, msg="请求来源不被允许")(
                scope, receive, private_send
            )
            return
        if write and path in {"/v1/admin/media/upload", "/v1/account/avatars/upload"}:
            # The authenticated handler bounds the binary stream; do not prebuffer it here.
            await self.app(scope, receive, private_send)
            return
        if write:
            if (
                path == "/v1/auth/login"
                and headers.get("content-type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                await JSONResponseFail(status_code=415, msg="请使用 JSON 请求")(
                    scope, receive, private_send
                )
                return
            messages: list[Message] = []
            length = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                length += len(message.get("body", b""))
                if length > (
                    1100000
                    if path
                    in {
                        "/v1/admin/pages/save",
                        "/v1/admin/pages/templates",
                        "/v1/admin/content/create",
                        "/v1/admin/content/save",
                        "/v1/admin/content/publish",
                        "/v1/admin/content/schedule",
                    }
                    else 1500000
                    if path == "/v1/admin/icons/upload"
                    else 65536
                    if path.startswith(("/v1/admin/", "/v1/plugins/"))
                    else 16384
                ):
                    await JSONResponseFail(status_code=413, msg="请求内容过大")(
                        scope, receive, private_send
                    )
                    return
                messages.append(message)
                if not message.get("more_body", False):
                    break
            queued = iter(messages)

            async def replay() -> Message:
                return next(queued, {"type": "http.request", "body": b"", "more_body": False})

            await self.app(scope, replay, private_send)
        else:
            await self.app(scope, receive, private_send)
