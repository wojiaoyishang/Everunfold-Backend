"""Private translation catalog and sparse override management."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.settings.router import Authorized
from app.modules.settings.translation_schemas import SaveTranslations, TranslationState
from app.modules.settings.translation_service import TranslationService

router = APIRouter(
    prefix="/v1/admin/settings/translations",
    tags=["settings"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 500, 503)
    },
)


@router.get("", response_model=PublicResponse[TranslationState])
async def read_translations(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await TranslationService(ctx.service.session, ctx.service.config).state()
    )


@router.patch("", response_model=PublicResponse[TranslationState])
async def save_translations(payload: SaveTranslations, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await TranslationService(ctx.service.session, ctx.service.config).save(
            payload, ctx.actor.id
        )
    )
