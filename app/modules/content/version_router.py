"""Authenticated, CSRF-protected version and merge operations."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.content import version_service as service
from app.modules.content.version_schemas import (
    BranchDetail,
    BranchLookup,
    BranchUpload,
    MergeBegin,
    MergeDetail,
    MergeLookup,
    MergeResolve,
    VersionCatalog,
    VersionDeleted,
    VersionDetail,
    VersionLookup,
    VersionQuery,
)
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/content/versions",
    tags=["content-versions"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 503)},
)


@router.post("/list", response_model=PublicResponse[VersionCatalog])
async def catalog(payload: VersionQuery, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service.catalog(ctx.service.session, payload))


@router.post("/read", response_model=PublicResponse[VersionDetail])
async def read(payload: VersionLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service.read_version(ctx.service.session, payload))


@router.post("/delete", response_model=PublicResponse[VersionDeleted])
async def remove(payload: VersionLookup, ctx: Authorized) -> JSONResponseSuccess:
    await service.read_version(ctx.service.session, payload, remove=True)
    return JSONResponseSuccess(data=VersionDeleted())


@router.post("/branch/upload", response_model=PublicResponse[BranchDetail])
async def upload_branch(payload: BranchUpload, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.upload_branch(
            ctx.service.session, ctx.service.config, ctx.actor, payload
        )
    )


@router.post("/branch/read", response_model=PublicResponse[BranchDetail])
async def read_branch(payload: BranchLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service.read_branch(ctx.service.session, payload))


@router.post("/branch/delete", response_model=PublicResponse[VersionDeleted])
async def delete_branch(payload: BranchLookup, ctx: Authorized) -> JSONResponseSuccess:
    await service.read_branch(ctx.service.session, payload, remove=True)
    return JSONResponseSuccess(data=VersionDeleted())


@router.post("/merge/begin", response_model=PublicResponse[MergeDetail])
async def begin(payload: MergeBegin, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.begin_merge(ctx.service.session, ctx.actor, payload)
    )


@router.post("/merge/read", response_model=PublicResponse[MergeDetail])
async def read_merge(payload: MergeLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=service.merge_detail(
            await service.owned_merge(ctx.service.session, ctx.actor, payload, active=False)
        )
    )


@router.post("/merge/resolve", response_model=PublicResponse[MergeDetail])
async def resolve(payload: MergeResolve, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.resolve_merge(ctx.service.session, ctx.actor, payload)
    )


@router.post("/merge/renew", response_model=PublicResponse[MergeDetail])
async def renew(payload: MergeLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.command_merge(
            ctx.service.session, ctx.service.config, ctx.actor, payload, "renew"
        )
    )


@router.post("/merge/cancel", response_model=PublicResponse[MergeDetail])
async def cancel(payload: MergeLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.command_merge(
            ctx.service.session, ctx.service.config, ctx.actor, payload, "cancel"
        )
    )


@router.post("/merge/finish", response_model=PublicResponse[MergeDetail])
async def finish(payload: MergeLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.command_merge(
            ctx.service.session, ctx.service.config, ctx.actor, payload, "finish"
        )
    )


@router.post("/merge/delete", response_model=PublicResponse[MergeDetail])
async def delete_merge(payload: MergeLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service.command_merge(
            ctx.service.session, ctx.service.config, ctx.actor, payload, "delete"
        )
    )
