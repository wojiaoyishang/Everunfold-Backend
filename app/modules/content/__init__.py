"""Public reading and private draft management; publishing remains a separate use case."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    """Only FastAPI calls this entrypoint; runtime requests never receive ctx."""
    from fastapi import APIRouter

    from app.modules.comments.router import router as comments_admin_router
    from app.modules.content.access_router import router as access_router
    from app.modules.content.collaboration.router import router as collaboration_router
    from app.modules.content.comment_likes import router as likes_router
    from app.modules.content.draft_router import router as drafts_router
    from app.modules.content.reader_preview import router as preview_router
    from app.modules.content.router import router
    from app.modules.content.translation_router import router as translation_router
    from app.modules.content.version_router import router as version_router

    assembled = APIRouter()
    assembled.include_router(comments_admin_router)
    assembled.include_router(router)
    assembled.include_router(preview_router)
    assembled.include_router(likes_router)
    assembled.include_router(drafts_router)
    assembled.include_router(access_router)
    assembled.include_router(collaboration_router)
    assembled.include_router(version_router)
    assembled.include_router(translation_router)
    return {"id": "content", "version": "0.1.0", "router": assembled}
