"""HTTP errors use one envelope, including errors emitted by file responses."""

from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.responses import JSONResponseFail


def http_error_response(status: int, headers: Mapping[str, str] | None = None) -> JSONResponseFail:
    messages = {
        404: "资源不存在",
        405: "请求方法不允许",
        422: "请求参数无效",
        503: "服务暂不可用",
    }
    return JSONResponseFail(
        msg=messages.get(status, "处理失败" if status >= 500 else "请求被拒绝"),
        status_code=status,
        headers=headers,
    )


class ErrorResponseMiddleware:
    """Replace non-JSON error bodies without buffering files or successful streams."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        replacement: JSONResponseFail | None = None
        delivered = False

        async def envelope_send(message: Message) -> None:
            nonlocal replacement, delivered
            if message["type"] == "http.response.start":
                content_type = Headers(scope=message).get("content-type", "").split(";", 1)[0]
                if message["status"] >= 400 and content_type != "application/json":
                    replacement = http_error_response(message["status"])
                    # Preserve duplicate Set-Cookie and protocol headers such as Allow,
                    # Retry-After and Content-Range; discard metadata for the old body.
                    old_body_headers = {
                        b"content-length",
                        b"content-type",
                        b"content-encoding",
                        b"content-disposition",
                        b"etag",
                        b"last-modified",
                    }
                    message = {
                        **message,
                        "headers": [
                            (key, value)
                            for key, value in message["headers"]
                            if key.lower() not in old_body_headers
                        ]
                        + replacement.raw_headers,
                    }
            elif replacement is not None and message["type"] == "http.response.body":
                if delivered:
                    return
                delivered = True
                message = {"type": "http.response.body", "body": replacement.body}
            await send(message)

        await self.app(scope, receive, envelope_send)
