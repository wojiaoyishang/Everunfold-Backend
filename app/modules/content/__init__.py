"""Public reading module; administration and collaboration are separate deliveries."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    """Only FastAPI calls this entrypoint; runtime requests never receive ctx."""
    from fastapi import APIRouter

    from app.modules.content.reader_preview import router as preview_router
    from app.modules.content.router import router

    assembled = APIRouter()
    assembled.include_router(router)
    assembled.include_router(preview_router)
    return {"id": "content", "version": "0.1.0", "router": assembled}
