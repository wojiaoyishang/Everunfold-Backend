"""Private administration APIs; browser calls FastAPI directly."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.business_auth import authorize_business
from app.modules.auth.permissions import Actor
from app.modules.auth.router import auth_runtime
from app.modules.settings.navigation import navigation_resources
from app.modules.settings.schemas import (
    NavigationCatalog,
    NavigationQuery,
    NodeList,
    PolicyState,
    SavePolicy,
    SaveSettings,
    SettingsState,
)
from app.modules.settings.service import SettingsService

router = APIRouter(
    prefix="/v1/admin",
    tags=["settings"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 415, 422, 500, 503)
    },
)


@dataclass
class Access:
    service: SettingsService
    actor: Actor


async def access(request: Request) -> AsyncIterator[Access]:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        user, readonly = await authorize_business(request, session, runtime)
        yield Access(SettingsService(session, runtime.settings), user)
        if user.auth_method == "api_key" and readonly and session.in_transaction():
            await session.commit()


Authorized = Annotated[Access, Depends(access, scope="function")]


@router.get("/settings", response_model=PublicResponse[SettingsState])
async def get_settings(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.state())


@router.post("/settings/navigation-resources", response_model=PublicResponse[NavigationCatalog])
async def query_navigation(payload: NavigationQuery, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await navigation_resources(ctx.service.session, payload))


@router.patch("/settings", response_model=PublicResponse[SettingsState])
async def save_settings(payload: SaveSettings, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.save(payload, ctx.actor.id))


@router.get("/frontend-nodes", response_model=PublicResponse[NodeList])
async def nodes(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.nodes())


@router.get("/frontend-nodes/{node_id}/cache-policy", response_model=PublicResponse[PolicyState])
async def policy(node_id: str, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.policy(node_id))


@router.patch("/frontend-nodes/{node_id}/cache-policy", response_model=PublicResponse[PolicyState])
async def save_policy(node_id: str, payload: SavePolicy, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.save_policy(node_id, payload, ctx.actor.id))


@router.post("/frontend-nodes/{node_id}/sync", response_model=PublicResponse[NodeList])
async def retry(node_id: str, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.retry(node_id, ctx.actor.id))
