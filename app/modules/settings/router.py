"""Private administration APIs; browser calls FastAPI directly."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.business_auth import authorize_business
from app.modules.auth.permissions import Actor
from app.modules.auth.router import auth_runtime, cookie
from app.modules.auth.service import AuthService
from app.modules.comments.discussion import save_discussion_settings, settings_from_data
from app.modules.comments.discussion_schema import DiscussionSettings
from app.modules.settings.navigation import navigation_resources
from app.modules.settings.page_injection_schemas import (
    PageInjectionRules,
    PageInjectionState,
    SavePageInjections,
)
from app.modules.settings.page_injections import (
    configured_rules,
    effective_rules,
    require_code_admin,
    save_rules,
)
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
PRIVATE_RULES = {"Cache-Control": "private, no-store"}


@router.get("/settings/page-injections", response_model=PublicResponse[PageInjectionState])
async def get_page_injections(ctx: Authorized) -> JSONResponseSuccess:
    require_code_admin(ctx.actor)
    return JSONResponseSuccess(
        data=configured_rules((await ctx.service.site()).data), headers=PRIVATE_RULES
    )


@router.patch("/settings/page-injections", response_model=PublicResponse[PageInjectionState])
async def update_page_injections(
    payload: SavePageInjections, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await save_rules(ctx.service, ctx.actor, payload), headers=PRIVATE_RULES
    )


@router.get("/page-injections", response_model=PublicResponse[PageInjectionRules])
async def admin_page_injections(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=effective_rules((await ctx.service.site()).data, "admin"), headers=PRIVATE_RULES
    )


@router.get("/page-injections/account", response_model=PublicResponse[PageInjectionRules])
async def account_page_injections(request: Request) -> JSONResponseSuccess:
    runtime = auth_runtime(request)
    async with runtime.database.session_factory() as session:
        user = await AuthService(
            session, runtime.settings.auth, runtime.password_limiter
        ).require_user(cookie(request), None, write=False)
        rules = (
            PageInjectionRules()
            if user.must_change_password
            else effective_rules(
                (await SettingsService(session, runtime.settings).site()).data, "account"
            )
        )
        return JSONResponseSuccess(data=rules, headers=PRIVATE_RULES)


@router.get("/settings", response_model=PublicResponse[SettingsState])
async def get_settings(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await ctx.service.state())


@router.get("/settings/discussion", response_model=PublicResponse[DiscussionSettings])
async def get_discussion_settings(ctx: Authorized) -> JSONResponseSuccess:
    site = await ctx.service.site()
    return JSONResponseSuccess(data=settings_from_data(site.data))


@router.patch("/settings/discussion", response_model=PublicResponse[DiscussionSettings])
async def update_discussion_settings(
    payload: DiscussionSettings, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await save_discussion_settings(ctx.service, payload, ctx.actor.id)
    )


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
