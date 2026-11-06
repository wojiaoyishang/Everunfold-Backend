"""Page management requires a live browser session; declaration installation is a deploy action."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_router import AccountAccess, Authorized
from app.modules.auth.permissions import user_permissions
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.pages.groups import translations
from app.modules.pages.metadata import PageMetadataService
from app.modules.pages.models import ManagedPage
from app.modules.pages.schemas import (
    CreatePageTranslation,
    LinkPageTranslation,
    ManagedPageCatalog,
    ManagedPageItem,
    PageMetadata,
    PageTranslationCatalog,
    PageTranslationLookup,
    PageTranslationMutation,
    RemoveManagedPage,
    SaveManagedPage,
    SavePageMetadata,
    SetPageEnabled,
)
from app.modules.pages.service import PagesService
from app.modules.pages.translations import PageTranslations

router = APIRouter(
    prefix="/v1/admin/pages",
    tags=["pages"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 429, 500, 503)
    },
)


async def reader(ctx: Authorized) -> AccountAccess:
    if "pages:read" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return ctx


Access = Annotated[AccountAccess, Depends(reader)]


def service(ctx: AccountAccess, request: Request) -> PagesService:
    # The runtime owns the fully loaded config, while AccountsService owns authentication.
    return PagesService(ctx.service, auth_runtime(request).settings)


@router.get("", response_model=PublicResponse[ManagedPageCatalog])
async def catalog(ctx: Access, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx, request).catalog())


@router.post("/save", response_model=PublicResponse[ManagedPageItem])
async def save(payload: SaveManagedPage, ctx: Access, request: Request) -> JSONResponseSuccess:
    if "pages:write" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return JSONResponseSuccess(data=await service(ctx, request).save(ctx.actor, payload))


@router.post("/remove", response_model=PublicResponse[ManagedPageItem])
async def remove(payload: RemoveManagedPage, ctx: Access, request: Request) -> JSONResponseSuccess:
    if "pages:write" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return JSONResponseSuccess(data=await service(ctx, request).remove(ctx.actor, payload))


@router.post("/enabled", response_model=PublicResponse[ManagedPageItem])
async def set_enabled(
    payload: SetPageEnabled, ctx: Access, request: Request
) -> JSONResponseSuccess:
    if "pages:write" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return JSONResponseSuccess(data=await service(ctx, request).set_enabled(ctx.actor, payload))


@router.post("/translations/query", response_model=PublicResponse[PageTranslationCatalog])
async def query_translations(payload: PageTranslationLookup, ctx: Access) -> JSONResponseSuccess:
    row = await ctx.service.session.get(ManagedPage, payload.id)
    if row is None:
        raise AuthFailure(404, 40496, "页面不存在")
    return JSONResponseSuccess(data=await translations(ctx.service.session, row))


async def change_translation(
    action: Literal["create", "link", "unlink", "review"],
    payload: PageTranslationMutation,
    ctx: AccountAccess,
    request: Request,
) -> JSONResponseSuccess:
    if "pages:write" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return JSONResponseSuccess(
        data=await PageTranslations(service(ctx, request)).mutate(action, ctx.actor, payload)
    )


@router.post("/translations/create", response_model=PublicResponse[PageTranslationCatalog])
async def create_translation(
    payload: CreatePageTranslation, ctx: Access, request: Request
) -> JSONResponseSuccess:
    return await change_translation("create", payload, ctx, request)


@router.post("/translations/link", response_model=PublicResponse[PageTranslationCatalog])
async def link_translation(
    payload: LinkPageTranslation, ctx: Access, request: Request
) -> JSONResponseSuccess:
    return await change_translation("link", payload, ctx, request)


@router.post("/translations/unlink", response_model=PublicResponse[PageTranslationCatalog])
async def unlink_translation(
    payload: PageTranslationMutation, ctx: Access, request: Request
) -> JSONResponseSuccess:
    return await change_translation("unlink", payload, ctx, request)


@router.post("/translations/review", response_model=PublicResponse[PageTranslationCatalog])
async def review_translation(
    payload: PageTranslationMutation, ctx: Access, request: Request
) -> JSONResponseSuccess:
    return await change_translation("review", payload, ctx, request)


@router.get("/{page_id}", response_model=PublicResponse[ManagedPageItem])
async def detail(page_id: UUID, ctx: Access, request: Request) -> JSONResponseSuccess:
    value = await ctx.service.session.scalar(select(ManagedPage).where(ManagedPage.id == page_id))
    if value is None:
        raise AuthFailure(404, 40496, "页面不存在")
    return JSONResponseSuccess(data=await service(ctx, request).item(value))


@router.get("/{page_id}/metadata", response_model=PublicResponse[PageMetadata])
async def read_metadata(page_id: UUID, ctx: Access) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await PageMetadataService(ctx.service).read(page_id))


@router.patch("/{page_id}/metadata", response_model=PublicResponse[PageMetadata])
async def save_metadata(
    page_id: UUID, payload: SavePageMetadata, ctx: Access
) -> JSONResponseSuccess:
    if "pages:write" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return JSONResponseSuccess(
        data=await PageMetadataService(ctx.service).save(ctx.actor, page_id, payload)
    )
