"""Private notification administration and current-account inbox."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.models import User
from app.modules.auth.router import Service, auth_runtime, cookie
from app.modules.auth.service import AuthFailure
from app.modules.notifications.catalog import SECURITY_EVENT_IDS
from app.modules.notifications.schemas import (
    MarkNotificationsRead,
    NotificationInbox,
    NotificationPreferences,
    NotificationState,
    SaveMailBrand,
    SaveNotificationRoutes,
    SaveNotificationTemplate,
)
from app.modules.notifications.service import NotificationService
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/notifications",
    tags=["notifications"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)
account_router = APIRouter(
    prefix="/v1/account/notifications",
    tags=["account notifications"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 422, 503)},
)


def service(ctx: Authorized) -> NotificationService:
    return NotificationService(ctx.service.session, ctx.service.config)


@router.get("/state", response_model=PublicResponse[NotificationState])
async def read_state(ctx: Authorized) -> JSONResponseSuccess:
    if not ctx.actor.has("settings:read"):
        raise AuthFailure(403, 40373, "无权读取通知配置")
    return JSONResponseSuccess(data=await service(ctx).state())


@router.patch("/routes", response_model=PublicResponse[NotificationState])
async def save_routes(payload: SaveNotificationRoutes, ctx: Authorized) -> JSONResponseSuccess:
    if not ctx.actor.has("settings:write"):
        raise AuthFailure(403, 40373, "无权修改通知策略")
    return JSONResponseSuccess(
        data=await service(ctx).save_routes(
            payload,
            ctx.actor.id,
            manage_access=ctx.actor.permissions.get("access:manage") == "all",
        )
    )


@router.patch("/templates", response_model=PublicResponse[NotificationState])
async def save_template(payload: SaveNotificationTemplate, ctx: Authorized) -> JSONResponseSuccess:
    if not ctx.actor.has("settings:write"):
        raise AuthFailure(403, 40373, "无权修改通知模板")
    if (
        payload.event_type in SECURITY_EVENT_IDS
        and ctx.actor.permissions.get("access:manage") != "all"
    ):
        raise AuthFailure(403, 40373, "无权修改账号安全通知模板")
    return JSONResponseSuccess(data=await service(ctx).save_template(payload, ctx.actor.id))


@router.patch("/mail-brand", response_model=PublicResponse[NotificationState])
async def save_brand(payload: SaveMailBrand, ctx: Authorized) -> JSONResponseSuccess:
    if not ctx.actor.has("settings:write"):
        raise AuthFailure(403, 40373, "无权修改邮件品牌")
    return JSONResponseSuccess(data=await service(ctx).save_brand(payload, ctx.actor.id))


@router.get("/inbox", response_model=PublicResponse[NotificationInbox])
async def inbox(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).inbox(ctx.actor.id))


@router.patch("/inbox", response_model=PublicResponse[NotificationInbox])
async def mark_read(payload: MarkNotificationsRead, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).mark_read(ctx.actor.id, payload))


async def account_user(request: Request, auth: Service) -> UUID:
    user = await auth.require_user(
        cookie(request),
        request.headers.get("X-CSRF-Token"),
        write=request.method == "PATCH",
    )
    return user.id


AccountUser = Annotated[UUID, Depends(account_user)]


@account_router.get("/preferences", response_model=PublicResponse[NotificationPreferences])
async def read_preferences(auth: Service, user_id: AccountUser) -> JSONResponseSuccess:
    user = await auth.session.get(User, user_id)
    if user is None or not user.active:
        raise AuthFailure(401, 40101, "请先登录")
    return JSONResponseSuccess(
        data=NotificationPreferences(comment_reply_email=user.comment_reply_email)
    )


@account_router.patch("/preferences", response_model=PublicResponse[NotificationPreferences])
async def save_preferences(
    payload: NotificationPreferences, auth: Service, user_id: AccountUser
) -> JSONResponseSuccess:
    user = await auth.session.scalar(
        select(User)
        .where(User.id == user_id, User.active.is_(True))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise AuthFailure(401, 40101, "请先登录")
    if payload.comment_reply_email and not user.email_verified_at:
        raise AuthFailure(422, 42299, "请先验证邮箱，再订阅评论回复邮件。")
    user.comment_reply_email = payload.comment_reply_email
    await auth.session.commit()
    return JSONResponseSuccess(data=payload)


@account_router.get("/inbox", response_model=PublicResponse[NotificationInbox])
async def account_inbox(
    request: Request, auth: Service, user_id: AccountUser
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await NotificationService(auth.session, auth_runtime(request).settings).inbox(user_id)
    )


@account_router.patch("/inbox", response_model=PublicResponse[NotificationInbox])
async def mark_account_read(
    payload: MarkNotificationsRead, request: Request, auth: Service, user_id: AccountUser
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await NotificationService(auth.session, auth_runtime(request).settings).mark_read(
            user_id, payload
        )
    )
