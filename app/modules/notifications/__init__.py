"""Notification administration routes, assembled only by the API process."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from fastapi import APIRouter

    from app.modules.notifications.router import router as notification_router

    router = APIRouter()
    router.include_router(notification_router)
    return {"id": "notifications", "version": "0.1.0", "router": router}
