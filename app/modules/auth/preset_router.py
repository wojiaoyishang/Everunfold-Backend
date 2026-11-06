"""Private versioned preset administration; application is part of user save/create."""

from uuid import UUID

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_router import Manager
from app.modules.auth.preset_schemas import (
    UserPreset,
    UserPresetCatalog,
    UserPresetCreate,
    UserPresetRemove,
    UserPresetSave,
)
from app.modules.auth.preset_service import UserPresetsService

router = APIRouter(
    prefix="/v1/admin/user-presets",
    tags=["user-presets"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 500, 503)
    },
)


@router.get("", response_model=PublicResponse[UserPresetCatalog])
async def catalog(ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await UserPresetsService(ctx.service).catalog(ctx.actor))


@router.post("", response_model=PublicResponse[UserPreset], status_code=201)
async def create(payload: UserPresetCreate, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await UserPresetsService(ctx.service).save(ctx.actor, payload), status_code=201
    )


@router.patch("/{preset_id}", response_model=PublicResponse[UserPreset])
async def save(preset_id: UUID, payload: UserPresetSave, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await UserPresetsService(ctx.service).save(ctx.actor, payload, preset_id)
    )


@router.post("/{preset_id}/remove", response_model=PublicResponse[UserPresetCatalog])
async def remove(preset_id: UUID, payload: UserPresetRemove, ctx: Manager) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await UserPresetsService(ctx.service).remove(ctx.actor, preset_id, payload)
    )
