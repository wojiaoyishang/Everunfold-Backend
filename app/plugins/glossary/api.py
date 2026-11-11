"""Public index and permission-scoped administration routes."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.plugin_sdk import JSONResponseSuccess, PluginRequest, PublicResponse

from .schemas import (
    ContentGlossaryMatches,
    GlossaryCreate,
    GlossaryDetail,
    GlossaryHookArchive,
    GlossaryHookCreate,
    GlossaryHookItem,
    GlossaryHookList,
    GlossaryHookQuery,
    GlossaryHookUpdate,
    GlossaryList,
    GlossaryMatchPreviewInput,
    GlossaryMatchPreviewOutput,
    GlossaryQuery,
    GlossaryUpdate,
    PublicGlossaryDetail,
    PublicGlossaryHooks,
    PublicGlossaryIndex,
)
from .service import (
    archive_hook,
    content_matches,
    create_hook,
    create_term,
    hook_detail,
    list_hooks,
    list_terms,
    preview_matches,
    public_hooks,
    public_index,
    public_term,
    read_term,
    update_hook,
    update_term,
)

public = APIRouter()
read_admin = APIRouter()
write_admin = APIRouter()


@public.get("/index", response_model=PublicResponse[PublicGlossaryIndex])
async def index(
    ctx: PluginRequest,
    language: str | None = Query(default=None, max_length=32),
) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await public_index(ctx.session, language, ctx.runtime.settings))


@public.get("/terms/{slug}", response_model=PublicResponse[PublicGlossaryDetail])
async def term(
    slug: str,
    ctx: PluginRequest,
    language: str | None = Query(default=None, max_length=32),
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await public_term(ctx.session, slug, language, ctx.runtime.settings)
    )


@public.get("/for-content/{content_id}", response_model=PublicResponse[ContentGlossaryMatches])
async def matches(content_id: UUID, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await content_matches(ctx.session, content_id, ctx.runtime.settings)
    )


@public.get("/hooks/for-target", response_model=PublicResponse[PublicGlossaryHooks])
async def hooks_for_target(
    ctx: PluginRequest,
    surface: Annotated[
        Literal["article", "shuoshuo", "page", "code"],
        Query(),
    ] = "code",
    content_id: Annotated[UUID | None, Query()] = None,
    page_id: Annotated[UUID | None, Query()] = None,
    language: Annotated[str | None, Query(max_length=32)] = None,
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await public_hooks(
            ctx.session,
            surface,
            content_id,
            page_id,
            language,
            ctx.runtime.settings,
        )
    )


@read_admin.post("/admin/query", response_model=PublicResponse[GlossaryList])
async def query(payload: GlossaryQuery, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await list_terms(ctx.session, payload, ctx.runtime.settings))


@read_admin.post("/admin/preview", response_model=PublicResponse[GlossaryMatchPreviewOutput])
async def preview(payload: GlossaryMatchPreviewInput, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=preview_matches(payload))


@read_admin.get("/admin/terms/{term_id}", response_model=PublicResponse[GlossaryDetail])
async def admin_term(term_id: UUID, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await read_term(ctx.session, term_id, ctx.runtime.settings))


@read_admin.post("/admin/hooks/query", response_model=PublicResponse[GlossaryHookList])
async def query_hooks(
    payload: GlossaryHookQuery,
    ctx: PluginRequest,
) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await list_hooks(ctx.session, payload))


@read_admin.get("/admin/hooks/{hook_id}", response_model=PublicResponse[GlossaryHookItem])
async def admin_hook(hook_id: UUID, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await hook_detail(ctx.session, hook_id))


@write_admin.post("/admin/terms", response_model=PublicResponse[GlossaryDetail])
async def create(payload: GlossaryCreate, ctx: PluginRequest) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(
        data=await create_term(ctx.session, ctx.actor, payload, ctx.runtime.settings)
    )


@write_admin.patch("/admin/terms/{term_id}", response_model=PublicResponse[GlossaryDetail])
async def update(term_id: UUID, payload: GlossaryUpdate, ctx: PluginRequest) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(
        data=await update_term(
            ctx.session,
            ctx.actor,
            term_id,
            payload,
            ctx.runtime.settings,
        )
    )


@write_admin.post("/admin/hooks", response_model=PublicResponse[GlossaryHookItem])
async def create_glossary_hook(
    payload: GlossaryHookCreate,
    ctx: PluginRequest,
) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(data=await create_hook(ctx.session, ctx.actor, payload))


@write_admin.patch("/admin/hooks/{hook_id}", response_model=PublicResponse[GlossaryHookItem])
async def update_glossary_hook(
    hook_id: UUID,
    payload: GlossaryHookUpdate,
    ctx: PluginRequest,
) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(data=await update_hook(ctx.session, ctx.actor, hook_id, payload))


@write_admin.post(
    "/admin/hooks/{hook_id}/archive",
    response_model=PublicResponse[GlossaryHookItem],
)
async def archive_glossary_hook(
    hook_id: UUID,
    payload: GlossaryHookArchive,
    ctx: PluginRequest,
) -> JSONResponseSuccess:
    assert ctx.actor is not None
    return JSONResponseSuccess(data=await archive_hook(ctx.session, ctx.actor, hook_id, payload))
