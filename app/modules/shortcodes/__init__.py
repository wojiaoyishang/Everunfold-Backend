"""Native shortcode module, assembled exclusively by the API process."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from fastapi import APIRouter

    from app.modules.shortcodes.region_router import router as regions
    from app.modules.shortcodes.router import router as admin

    router = APIRouter()
    router.include_router(admin)
    router.include_router(regions)

    return {"id": "shortcodes", "version": "0.1.0", "router": router}
