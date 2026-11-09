"""Private administrator SMTP settings, using existing authorization and CSRF guards."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.settings.mail_schemas import MailSettingsState, SaveMailSettings
from app.modules.settings.mail_service import MailSettingsService
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/settings/mail",
    tags=["settings"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)


@router.get("", response_model=PublicResponse[MailSettingsState])
async def read_mail(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await MailSettingsService(ctx.service).state())


@router.patch("", response_model=PublicResponse[MailSettingsState])
async def save_mail(payload: SaveMailSettings, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MailSettingsService(ctx.service).save(payload, ctx.actor.id)
    )
