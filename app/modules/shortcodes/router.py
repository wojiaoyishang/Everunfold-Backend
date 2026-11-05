"""All template administration uses the existing live-role, CSRF-checked boundary."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicResponse
from app.modules.settings.router import Authorized
from app.modules.shortcodes.schemas import (
    PreviewShortcode,
    RestoreShortcode,
    SaveShortcode,
    ShortcodeAction,
    ShortcodeCatalog,
    ShortcodeCompilation,
    ShortcodeItem,
)
from app.modules.shortcodes.service import ShortcodeService

router = APIRouter(prefix="/v1/admin/shortcodes", tags=["shortcodes"])


def service(ctx: Authorized) -> ShortcodeService:
    return ShortcodeService(ctx.service.session, ctx.service.config)


@router.get("", response_model=PublicResponse[ShortcodeCatalog])
async def catalog(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).catalog())


@router.post("/save", response_model=PublicResponse[ShortcodeItem])
async def save(payload: SaveShortcode, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).save(payload, ctx.actor.id))


@router.post("/preview", response_model=PublicResponse[ShortcodeCompilation])
async def preview(payload: PreviewShortcode, ctx: Authorized) -> JSONResponseSuccess:
    from app.modules.auth.service import AuthFailure

    try:
        result = ShortcodeCompilation(compiled=await service(ctx).preview(payload), error="")
    except AuthFailure as error:
        if error.status != 422:
            raise
        result = ShortcodeCompilation(compiled=None, error=error.message)
    return JSONResponseSuccess(data=result)


@router.post("/apply", response_model=PublicResponse[ShortcodeCatalog])
async def apply(payload: ShortcodeAction, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).apply(payload, ctx.actor.id))


@router.post("/archive", response_model=PublicResponse[ShortcodeCatalog])
async def archive(payload: ShortcodeAction, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).archive(payload, ctx.actor.id))


@router.post("/restore", response_model=PublicResponse[ShortcodeItem])
async def restore(payload: RestoreShortcode, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx).restore(payload, ctx.actor.id))
