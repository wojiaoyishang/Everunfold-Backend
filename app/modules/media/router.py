"""Authenticated management and read-only serving of committed public icon originals."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from starlette.responses import FileResponse

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.media.management import MediaManagement
from app.modules.media.media_validation import MAX_MEDIA_BYTES
from app.modules.media.models import MediaAsset
from app.modules.media.removal import MediaRemovalService
from app.modules.media.schemas import (
    IconAsset,
    IconCatalog,
    IconQuery,
    IconUpload,
    MediaCatalog,
    MediaDetails,
    MediaEdit,
    MediaItem,
    MediaLookup,
    MediaQuery,
    MediaRemoval,
    MediaRemove,
    MediaUpdate,
    MediaUpload,
)
from app.modules.media.service import MediaService
from app.modules.media.storage import LocalObjectStorage
from app.modules.settings.router import Authorized

router = APIRouter(
    tags=["media"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 500, 503)
    },
)


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


@router.post("/v1/admin/media/query", response_model=PublicResponse[MediaCatalog])
async def media_catalog(query: MediaQuery, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaService(ctx.service.session, ctx.service.config).catalog_media(query)
    )


@router.post("/v1/admin/media/upload", response_model=PublicResponse[MediaItem])
async def media_upload(
    request: Request, metadata: Annotated[MediaUpload, Query()], ctx: Authorized
) -> JSONResponseSuccess:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/octet-stream"
    ):
        raise AuthFailure(415, 41530, "请上传文件原始字节")
    # Authentication and CSRF are resolved before reading this bounded binary body.
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_MEDIA_BYTES:
            raise AuthFailure(413, 41330, "单个文件不超过 128 MB")
        chunks.append(chunk)
    data = b"".join(chunks)
    return JSONResponseSuccess(
        data=await MediaService(ctx.service.session, ctx.service.config).upload_media(
            metadata, data, ctx.actor.id
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
            filename=asset.filename if asset.mime == "application/octet-stream" else None,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'none'; sandbox",
                "Cache-Control": "public, max-age=3600",
                "Cross-Origin-Resource-Policy": "cross-origin",
            },
        )


@router.post("/v1/admin/media/detail", response_model=PublicResponse[MediaDetails])
async def media_detail(payload: MediaLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaManagement(ctx.service.session, ctx.service.config).details(payload.id)
    )


@router.post("/v1/admin/media/update", response_model=PublicResponse[MediaDetails])
async def media_update(payload: MediaUpdate, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaManagement(ctx.service.session, ctx.service.config).update(
            payload, ctx.actor.id
        )
    )


@router.post("/v1/admin/media/edit", response_model=PublicResponse[MediaDetails])
async def media_edit(payload: MediaEdit, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaManagement(ctx.service.session, ctx.service.config).edit(
            payload, ctx.actor.id
        )
    )


@router.post("/v1/admin/media/remove", response_model=PublicResponse[MediaRemoval])
async def media_remove(payload: MediaRemove, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaRemovalService(ctx.service.session, ctx.service.config).remove(
            payload, ctx.actor.id
        )
    )


@router.post("/v1/admin/media/restore", response_model=PublicResponse[MediaRemoval])
async def media_restore(payload: MediaLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MediaRemovalService(ctx.service.session, ctx.service.config).restore(
            payload, ctx.actor.id
        )
    )
