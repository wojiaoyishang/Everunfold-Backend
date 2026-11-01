"""Authenticated management and read-only serving of committed public icon originals."""

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import FileResponse

from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.media.models import MediaAsset
from app.modules.media.schemas import IconAsset, IconCatalog, IconQuery, IconUpload
from app.modules.media.service import MediaService
from app.modules.media.storage import LocalObjectStorage
from app.modules.settings.router import Authorized

router = APIRouter(tags=["media"])


@router.post("/v1/admin/icons/query", response_model=PublicResponse[IconCatalog])
async def icons(query: IconQuery, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaService(ctx.service.session, ctx.service.config).catalog(query)
    )


@router.post("/v1/admin/icons/upload", response_model=PublicResponse[IconAsset])
async def upload(payload: IconUpload, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaService(ctx.service.session, ctx.service.config).upload(
            payload, ctx.actor.id
        )
    )


@router.get("/media/{object_key:path}", include_in_schema=False)
async def serve(object_key: str, request: Request) -> FileResponse:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        asset = await session.scalar(
            select(MediaAsset).where(
                MediaAsset.object_key == object_key,
                MediaAsset.status == "ready",
                MediaAsset.deleted_at.is_(None),
            )
        )
        if asset is None:
            raise AuthFailure(404, 40430, "资源不存在")
        path = LocalObjectStorage(runtime.settings.uploads_directory).path(asset.object_key)
        if not path.is_file():
            raise AuthFailure(404, 40430, "资源不存在")
        return FileResponse(
            path,
            media_type=asset.mime,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'none'; sandbox",
                "Cache-Control": "public, max-age=3600",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )
