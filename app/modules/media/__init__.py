"""First-party media registration; only API assembly calls router_init."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from app.modules.media.router import router

    return {"id": "media", "version": "0.1.0", "router": router}
