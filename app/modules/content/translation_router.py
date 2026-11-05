"""Authorized language membership operations, independent from CRDT text updates."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.content.draft_schemas import (
    DraftLookup,
    TranslationCatalog,
    TranslationCreate,
    TranslationGroupMutation,
    TranslationLink,
    TranslationMutation,
)
from app.modules.content.translations import Translations
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/content/translations",
    tags=["content-management"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)


@router.post("/query", response_model=PublicResponse[TranslationCatalog])
async def query(payload: DraftLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).read(payload.id)
    )


@router.post("/create", response_model=PublicResponse[TranslationCatalog])
async def create(payload: TranslationCreate, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).mutate(
            "create", payload, ctx.actor
        )
    )


@router.post("/link", response_model=PublicResponse[TranslationCatalog])
async def link(payload: TranslationLink, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).mutate(
            "link", payload, ctx.actor
        )
    )


@router.post("/unlink", response_model=PublicResponse[TranslationCatalog])
async def unlink(payload: TranslationMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).mutate(
            "unlink", payload, ctx.actor
        )
    )


@router.post("/review", response_model=PublicResponse[TranslationCatalog])
async def review(payload: TranslationMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).mutate(
            "review", payload, ctx.actor
        )
    )


@router.post("/trash", response_model=PublicResponse[TranslationCatalog])
async def trash(payload: TranslationGroupMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).mutate(
            "trash", payload, ctx.actor
        )
    )


@router.post("/restore", response_model=PublicResponse[TranslationCatalog])
async def restore(payload: TranslationGroupMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await Translations(ctx.service.session, ctx.service.config).mutate(
            "restore", payload, ctx.actor
        )
    )
