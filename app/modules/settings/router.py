"""Private administration APIs; browser calls FastAPI directly."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.models import User
from app.modules.auth.router import auth_runtime, cookie
from app.modules.auth.service import AuthService
from app.modules.settings.schemas import (
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
    actor: User


async def access(request: Request) -> AsyncIterator[Access]:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        auth = AuthService(session, runtime.settings.auth, runtime.password_limiter)
        user = await auth.require_admin(
            cookie(request),
            request.headers.get("X-CSRF-Token") if request.method != "GET" else None,
            write=request.method != "GET",
        )
        yield Access(SettingsService(session, runtime.settings), user)


Authorized = Annotated[Access, Depends(access)]


@router.get("/settings", response_model=PublicResponse[SettingsState])
async def get_settings(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.state())


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
    return JSONResponseSuccess(data=await ctx.service.retry(node_id))
