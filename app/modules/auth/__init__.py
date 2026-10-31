"""First-party account and browser-session module."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from app.modules.auth.router import router

    return {"id": "auth", "version": "0.1.0", "router": router}
