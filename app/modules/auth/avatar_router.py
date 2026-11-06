"""Private avatar picker/upload without granting access to the administration media library."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_router import Authorized, Manager
from app.modules.auth.avatar_schemas import (
    AvatarCatalog,
    AvatarItem,
    AvatarPolicySave,
    AvatarPolicyState,
    AvatarRemove,
    AvatarUpload,
)
from app.modules.auth.avatar_service import AvatarService
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure

router = APIRouter(
    prefix="/v1",
    tags=["account-avatars"],
    responses={
        status: {"model": ErrorResponse}
        for status in (401, 403, 404, 409, 413, 415, 422, 429, 500, 503)
    },
)


@router.get("/account/avatars", response_model=PublicResponse[AvatarCatalog])
async def avatars(request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await AvatarService(ctx.service, auth_runtime(request).settings).catalog(ctx.actor)
    )


@router.post("/account/avatars/upload", response_model=PublicResponse[AvatarItem])
async def upload(
    request: Request, metadata: Annotated[AvatarUpload, Query()], ctx: Authorized
) -> JSONResponseSuccess:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].lower()
        != "application/octet-stream"
    ):
        raise AuthFailure(415, 41530, "请上传文件原始字节")
    service = AvatarService(ctx.service, auth_runtime(request).settings)
    limits = await service.policy()
    # Authentication precedes a bounded stream; there is no media-management dependency here.
    await ctx.service.session.commit()
    chunks, size = [], 0
    async for part in request.stream():
        size += len(part)
        if size > limits.max_bytes:
            raise AuthFailure(413, 41365, "头像文件超过当前上传限制")
        chunks.append(part)
    return JSONResponseSuccess(data=await service.upload(ctx.actor, metadata, b"".join(chunks)))


@router.post("/account/avatars/remove", response_model=PublicResponse[AvatarCatalog])
async def remove(payload: AvatarRemove, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    service = AvatarService(ctx.service, auth_runtime(request).settings)
    await service.remove(ctx.actor, payload.avatar_id, payload.operation_id)
    return JSONResponseSuccess(data=await service.catalog(ctx.actor))


@router.get("/admin/users/avatar-policy", response_model=PublicResponse[AvatarPolicyState])
async def policy(request: Request, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await AvatarService(ctx.service, auth_runtime(request).settings).policy()
    )


@router.patch("/admin/users/avatar-policy", response_model=PublicResponse[AvatarPolicyState])
async def save_policy(
    payload: AvatarPolicySave, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await AvatarService(ctx.service, auth_runtime(request).settings).save_policy(
            ctx.actor, payload
        )
    )


@router.get("/admin/users/{user_id}/avatars", response_model=PublicResponse[AvatarCatalog])
async def user_avatars(user_id: UUID, request: Request, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await AvatarService(ctx.service, auth_runtime(request).settings).catalog(
            await ctx.service.target(user_id)
        )
    )


@router.post("/admin/users/{user_id}/avatars/remove", response_model=PublicResponse[AvatarCatalog])
async def remove_user_avatar(
    user_id: UUID, payload: AvatarRemove, request: Request, ctx: Manager
) -> JSONResponseSuccess:
    owner = await ctx.service.target(user_id)
    if owner.role == "admin" and ctx.actor.role != "admin":
        raise AuthFailure(403, 40360, "不能修改管理员账号")
    service = AvatarService(ctx.service, auth_runtime(request).settings)
    await service.remove(ctx.actor, payload.avatar_id, payload.operation_id, owner=owner)
    return JSONResponseSuccess(data=await service.catalog(owner))
