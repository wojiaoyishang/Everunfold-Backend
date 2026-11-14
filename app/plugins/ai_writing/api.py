"""Browser-session-only AI writing API, registered through the plugin SDK."""

from typing import cast
from uuid import UUID

from fastapi import APIRouter

from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.plugin_sdk import JSONResponseSuccess, PluginRequest, PluginRequestContext, PublicResponse

from .models import AiWritingRequest
from .schemas import (
    AiAvailability,
    AiConfiguration,
    AiGeneration,
    GenerateAiText,
    SaveAiConfiguration,
    Scene,
    TestAiConnection,
)
from .service import (
    availability,
    configuration,
    configuration_view,
    enqueue,
    request_view,
    save_configuration,
    target_access,
)

router = APIRouter()


def account(ctx: PluginRequestContext, *, configure: bool = False) -> Actor:
    actor = ctx.actor
    if actor is None or (configure and not actor.allows("settings:write")):
        raise AuthFailure(403, 40389, "没有管理 AI 写作配置的权限")
    return actor


@router.get("/configuration", response_model=PublicResponse[AiConfiguration])
async def read_configuration(ctx: PluginRequest) -> JSONResponseSuccess:
    account(ctx, configure=True)
    return JSONResponseSuccess(data=configuration_view(await configuration(ctx.session)))


@router.put("/configuration", response_model=PublicResponse[AiConfiguration])
async def update_configuration(
    payload: SaveAiConfiguration, ctx: PluginRequest
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await save_configuration(
            ctx.session, ctx.runtime.settings, account(ctx, configure=True), payload
        )
    )


@router.get("/availability", response_model=PublicResponse[AiAvailability])
async def read_availability(ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await availability(ctx.session, account(ctx)))


@router.post("/generations", response_model=PublicResponse[AiGeneration])
async def create_generation(payload: GenerateAiText, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await enqueue(ctx.session, ctx.runtime.settings, account(ctx), payload)
    )


@router.post("/test", response_model=PublicResponse[AiGeneration])
async def test_connection(payload: TestAiConnection, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await enqueue(ctx.session, ctx.runtime.settings, account(ctx, configure=True), payload)
    )


@router.get("/generations/{identity}", response_model=PublicResponse[AiGeneration])
async def read_generation(identity: UUID, ctx: PluginRequest) -> JSONResponseSuccess:
    actor = account(ctx)
    row = await ctx.session.get(AiWritingRequest, identity)
    if row is None or row.actor_id != actor.id:
        raise AuthFailure(404, 40489, "生成请求不存在")
    if row.scene is None:
        account(ctx, configure=True)
    else:
        assert row.field is not None
        await target_access(
            ctx.session,
            ctx.runtime.settings,
            actor,
            cast(Scene, row.scene),
            row.field,
            row.resource_id,
        )
    return JSONResponseSuccess(data=request_view(ctx.runtime.settings, row))
