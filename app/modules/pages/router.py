"""Page management requires a live browser session; declaration installation is a deploy action."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import select

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.accounts_router import AccountAccess, Authorized
from app.modules.auth.permissions import user_permissions
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.content.models import Taxonomy
from app.modules.pages.groups import source_value, translations
from app.modules.pages.metadata import PageMetadataService
from app.modules.pages.models import ManagedPage
from app.modules.pages.schemas import (
    ApplyPageTemplate,
    CreatePageTranslation,
    LinkPageTranslation,
    ManagedPageCatalog,
    ManagedPageItem,
    ManagedPageVersion,
    PageCategories,
    PageCategory,
    PageMetadata,
    PageTemplateItem,
    PageTemplateVersion,
    PageTranslationCatalog,
    PageTranslationLookup,
    PageTranslationMutation,
    PageTypeCatalog,
    PageVersionCatalog,
    PageVersionQuery,
    RemoveManagedPage,
    RestorePageVersion,
    SaveManagedPage,
    SavePageMetadata,
    SavePageTemplate,
    SetPageEnabled,
    TemplateImpact,
    TitledPageCode,
)
from app.modules.pages.service import PagesService
from app.modules.pages.templates import PageTemplates, page_template
from app.modules.pages.translations import PageTranslations
from app.modules.pages.types import page_types
from app.modules.pages.versions import get_version, version_catalog, version_metadata

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


async def writer(ctx: Access) -> AccountAccess:
    if "pages:write" not in user_permissions(ctx.actor):
        raise AuthFailure(403, 40364, "没有执行此操作的权限")
    return ctx


WriteAccess = Annotated[AccountAccess, Depends(writer)]
VersionNumber = Annotated[int, Path(ge=1)]
VersionQuery = Annotated[PageVersionQuery, Query()]


def service(ctx: AccountAccess, request: Request) -> PagesService:
    # The runtime owns the fully loaded config, while AccountsService owns authentication.
    return PagesService(ctx.service, auth_runtime(request).settings)


@router.get("", response_model=PublicResponse[ManagedPageCatalog])
async def catalog(ctx: Access, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await service(ctx, request).catalog())


@router.get("/categories", response_model=PublicResponse[PageCategories])
async def categories(ctx: Access) -> JSONResponseSuccess:
    rows = await ctx.service.session.execute(
        select(Taxonomy.id, Taxonomy.name, Taxonomy.slug)
        .where(Taxonomy.kind == "category", Taxonomy.retired.is_(False))
        .order_by(Taxonomy.name, Taxonomy.id)
    )
    return JSONResponseSuccess(
        data=PageCategories(
            items=[PageCategory(id=identity, name=name, slug=slug) for identity, name, slug in rows]
        )
    )


@router.get("/types", response_model=PublicResponse[PageTypeCatalog])
async def types(ctx: Access, request: Request) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await page_types(ctx.service.session, auth_runtime(request).settings)
    )


@router.get("/templates/{template_id}", response_model=PublicResponse[PageTemplateItem])
async def read_template(template_id: UUID, ctx: Access, request: Request) -> JSONResponseSuccess:
    template = await page_template(ctx.service.session, template_id)
    templates = PageTemplates(ctx.service, auth_runtime(request).settings)
    return JSONResponseSuccess(data=await templates.item(template))


@router.post("/templates", response_model=PublicResponse[PageTemplateItem])
async def save_template(
    payload: SavePageTemplate, ctx: WriteAccess, request: Request
) -> JSONResponseSuccess:
    templates = PageTemplates(ctx.service, auth_runtime(request).settings)
    return JSONResponseSuccess(data=await templates.save(ctx.actor, payload))


@router.get("/templates/{template_id}/versions", response_model=PublicResponse[PageVersionCatalog])
async def template_versions(
    template_id: UUID, ctx: Access, query: VersionQuery
) -> JSONResponseSuccess:
    session = ctx.service.session
    return JSONResponseSuccess(
        data=await version_catalog(session, await page_template(session, template_id), query.before)
    )


@router.get(
    "/templates/{template_id}/versions/{revision}",
    response_model=PublicResponse[PageTemplateVersion],
)
async def template_version(
    template_id: UUID, revision: VersionNumber, ctx: Access
) -> JSONResponseSuccess:
    row = await get_version(ctx.service.session, "template", template_id, revision)
    return JSONResponseSuccess(
        data=PageTemplateVersion(
            version=version_metadata(row), value=TitledPageCode.model_validate(row.value)
        )
    )


@router.post(
    "/templates/{template_id}/versions/restore", response_model=PublicResponse[PageTemplateItem]
)
async def restore_template_version(
    template_id: UUID, payload: RestorePageVersion, ctx: WriteAccess, request: Request
) -> JSONResponseSuccess:
    templates = PageTemplates(ctx.service, auth_runtime(request).settings)
    return JSONResponseSuccess(
        data=await templates.restore_version(ctx.actor, template_id, payload)
    )


@router.get("/templates/{template_id}/impact", response_model=PublicResponse[TemplateImpact])
async def template_impact(template_id: UUID, ctx: Access, request: Request) -> JSONResponseSuccess:
    templates = PageTemplates(ctx.service, auth_runtime(request).settings)
    template = await page_template(ctx.service.session, template_id)
    return JSONResponseSuccess(
        data=templates.impact(template, await templates.affected_pages(template_id))
    )


@router.post("/templates/{template_id}/apply", response_model=PublicResponse[PageTemplateItem])
async def apply_template(
    template_id: UUID, payload: ApplyPageTemplate, ctx: WriteAccess, request: Request
) -> JSONResponseSuccess:
    templates = PageTemplates(ctx.service, auth_runtime(request).settings)
    return JSONResponseSuccess(data=await templates.apply(ctx.actor, template_id, payload))


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
    return JSONResponseSuccess(
        data=await service(ctx, request).item(await require_page(page_id, ctx))
    )


async def require_page(page_id: UUID, ctx: AccountAccess) -> ManagedPage:
    value = await ctx.service.session.get(ManagedPage, page_id)
    if value is None:
        raise AuthFailure(404, 40496, "页面不存在")
    return value


@router.get("/{page_id}/versions", response_model=PublicResponse[PageVersionCatalog])
async def page_versions(page_id: UUID, ctx: Access, query: VersionQuery) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await version_catalog(
            ctx.service.session, await require_page(page_id, ctx), query.before
        )
    )


@router.get("/{page_id}/versions/{revision}", response_model=PublicResponse[ManagedPageVersion])
async def page_version(page_id: UUID, revision: VersionNumber, ctx: Access) -> JSONResponseSuccess:
    row = await get_version(ctx.service.session, "page", page_id, revision)
    return JSONResponseSuccess(
        data=ManagedPageVersion(version=version_metadata(row), value=source_value(row.value))
    )


@router.post("/{page_id}/versions/restore", response_model=PublicResponse[ManagedPageItem])
async def restore_page_version(
    page_id: UUID, payload: RestorePageVersion, ctx: WriteAccess, request: Request
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await service(ctx, request).restore_version(ctx.actor, page_id, payload)
    )


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
