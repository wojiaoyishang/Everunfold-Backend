"""First-party settings module, assembled only by the API process."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from fastapi import APIRouter

    from app.modules.settings.extensions_router import router as extensions_router
    from app.modules.settings.mail_router import router as mail_router
    from app.modules.settings.progress_router import router as progress_router
    from app.modules.settings.router import router as settings_router
    from app.modules.settings.runtime_router import router as runtime_router

    router = APIRouter()
    router.include_router(mail_router)
    router.include_router(progress_router)
    router.include_router(runtime_router)
    router.include_router(settings_router)
    router.include_router(extensions_router)

    return {"id": "settings", "version": "0.1.0", "router": router}
