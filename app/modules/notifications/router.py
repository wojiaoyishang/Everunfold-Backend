"""Private notification administration and current-account inbox."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.service import AuthFailure
from app.modules.notifications.catalog import SECURITY_EVENT_IDS
from app.modules.notifications.schemas import (
    MarkNotificationsRead,
    NotificationInbox,
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
            payload, ctx.actor.id, manage_access=ctx.actor.has("access:manage")
        )
    )


@router.patch("/templates", response_model=PublicResponse[NotificationState])
async def save_template(payload: SaveNotificationTemplate, ctx: Authorized) -> JSONResponseSuccess:
    if not ctx.actor.has("settings:write"):
        raise AuthFailure(403, 40373, "无权修改通知模板")
    if payload.event_type in SECURITY_EVENT_IDS and not ctx.actor.has("access:manage"):
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
