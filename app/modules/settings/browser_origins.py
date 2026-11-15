"""Resolve browser origins from the deployment-managed node registry."""

from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.runtime import Runtime
from app.modules.settings.node_registry import registry_directory, registry_record


class ManagedBrowserOrigins:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        origin = (
            Headers(scope=scope).get("origin") if scope["type"] in {"http", "websocket"} else None
        )
        if origin and str(scope.get("path", "")).startswith(("/v1/", "/ws/", "/media/")):
            runtime: Runtime | None = getattr(scope["app"].state, "runtime", None)
            if runtime and runtime.settings.cors.use_frontend_registry:
                settings = runtime.settings
                if origin not in settings.cors.allowed_origins and await is_managed_origin(
                    runtime, origin
                ):
                    scope.setdefault("state", {})["managed_browser_origin"] = origin
        await self.app(scope, receive, send)


async def is_managed_origin(runtime: Runtime, origin: str) -> bool:
    try:
        async with runtime.database.session_factory() as session:
            record = await registry_record(session)
            if record is None:
                return False
            return any(
                node.enabled and canonical_origin(node.base_url) == origin
                for node in registry_directory(record).targets.values()
            )
    except (SQLAlchemyError, OSError, ValueError):
        return False


def canonical_origin(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    host = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.port is not None and parsed.port != default_port:
        host += f":{parsed.port}"
    return f"{parsed.scheme}://{host}"


def managed_origin(scope: Scope) -> str | None:
    value: object = scope.get("state", {}).get("managed_browser_origin")
    return value if isinstance(value, str) else None
