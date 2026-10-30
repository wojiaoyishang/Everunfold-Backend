"""Public reading module; administration and collaboration are separate deliveries."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    """Only FastAPI calls this entrypoint; runtime requests never receive ctx."""
    from app.modules.content.router import router

    return {"id": "content", "version": "0.1.0", "router": router}
