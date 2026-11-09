"""Private administrator SMTP settings, using existing authorization and CSRF guards."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.settings.mail_schemas import (
    MailSettingsState,
    MailTestLookup,
    MailTestState,
    SaveMailSettings,
    SendTestMail,
)
from app.modules.settings.mail_service import MailSettingsService
from app.modules.settings.mail_test_service import MailTestService
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/settings/mail",
    tags=["settings"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 500, 503)
    },
)


@router.get("", response_model=PublicResponse[MailSettingsState])
async def read_mail(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await MailSettingsService(ctx.service).state())


@router.patch("", response_model=PublicResponse[MailSettingsState])
async def save_mail(payload: SaveMailSettings, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MailSettingsService(ctx.service).save(payload, ctx.actor.id)
    )


@router.post("/test", response_model=PublicResponse[MailTestState])
async def send_test_mail(payload: SendTestMail, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MailTestService(ctx.service).request(payload, ctx.actor.id)
    )


@router.post("/test/status", response_model=PublicResponse[MailTestState])
async def read_test_mail(payload: MailTestLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await MailTestService(ctx.service).read(payload.id, ctx.actor.id)
    )
