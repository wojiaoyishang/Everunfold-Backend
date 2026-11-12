"""First-party account and browser-session module."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from fastapi import APIRouter

    from app.modules.auth.access_router import router as access_router
    from app.modules.auth.accounts_router import router as accounts_router
    from app.modules.auth.avatar_router import router as avatar_router
    from app.modules.auth.credentials_router import router as credentials_router
    from app.modules.auth.preset_router import router as preset_router
    from app.modules.auth.router import router

    combined = APIRouter()
    combined.include_router(router)
    combined.include_router(avatar_router)
    combined.include_router(accounts_router)
    combined.include_router(access_router)
    combined.include_router(credentials_router)
    combined.include_router(preset_router)

    return {"id": "auth", "version": "0.1.0", "router": combined}
