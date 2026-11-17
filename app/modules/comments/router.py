"""Private comment operations share authentication, CSRF and capability enforcement."""

from fastapi import APIRouter, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import auth_runtime
from app.modules.comments.schemas import (
    CommentQuery,
    CommentResult,
    EditComment,
    ModerateComments,
    ModerationCatalog,
    WorkspaceAnalytics,
    WorkspaceAnalyticsQuery,
    WorkspaceLayoutValue,
    WorkspaceSummary,
)
from app.modules.comments.service import CommentService
from app.modules.comments.workspace import workspace
from app.modules.comments.workspace_analytics import analytics
from app.modules.comments.workspace_layout import save_layout
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin",
    tags=["operations"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)


def service(ctx: Authorized, request: Request) -> CommentService:
    return CommentService(
        ctx.service.session, ctx.service.config, auth_runtime(request).password_limiter
    )


@router.post("/comments/query", response_model=PublicResponse[ModerationCatalog])
async def query(payload: CommentQuery, ctx: Authorized, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx, request).query(payload))


@router.post("/comments/moderate", response_model=PublicResponse[CommentResult])
async def moderate(
    payload: ModerateComments, ctx: Authorized, request: Request
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service(ctx, request).mutate(ctx.actor, "moderate", payload)
    )


@router.post("/comments/edit", response_model=PublicResponse[CommentResult])
async def edit(payload: EditComment, ctx: Authorized, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx, request).mutate(ctx.actor, "edit", payload))


@router.post("/comments/reply", response_model=PublicResponse[CommentResult])
async def reply(payload: EditComment, ctx: Authorized, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx, request).mutate(ctx.actor, "reply", payload))


@router.get("/workspace", response_model=PublicResponse[WorkspaceSummary])
async def overview(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await workspace(ctx.service.session, ctx.actor, ctx.service.config)
    )


@router.post("/workspace/analytics", response_model=PublicResponse[WorkspaceAnalytics])
async def workspace_analytics(
    payload: WorkspaceAnalyticsQuery, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await analytics(ctx.service.session, ctx.actor, payload.days))


@router.post("/workspace/layout", response_model=PublicResponse[WorkspaceLayoutValue])
async def workspace_layout(payload: WorkspaceLayoutValue, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await save_layout(ctx.service.session, ctx.actor, payload))
