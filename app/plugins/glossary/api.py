"""Public index and permission-scoped administration routes."""

from uuid import UUID

from fastapi import APIRouter, Query

from app.plugin_sdk import JSONResponseSuccess, PluginRequest, PublicResponse

from .schemas import (
    ContentGlossaryMatches,
    GlossaryCreate,
    GlossaryDetail,
    GlossaryList,
    GlossaryMatchPreviewInput,
    GlossaryMatchPreviewOutput,
    GlossaryQuery,
    GlossaryUpdate,
    PublicGlossaryDetail,
    PublicGlossaryIndex,
)
from .service import (
    content_matches,
    create_term,
    list_terms,
    preview_matches,
    public_index,
    public_term,
    read_term,
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
    return JSONResponseSuccess(data=await public_index(ctx.session, language))


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


@read_admin.post("/admin/query", response_model=PublicResponse[GlossaryList])
async def query(payload: GlossaryQuery, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await list_terms(ctx.session, payload))


@read_admin.post("/admin/preview", response_model=PublicResponse[GlossaryMatchPreviewOutput])
async def preview(payload: GlossaryMatchPreviewInput, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=preview_matches(payload))


@read_admin.get("/admin/terms/{term_id}", response_model=PublicResponse[GlossaryDetail])
async def admin_term(term_id: UUID, ctx: PluginRequest) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await read_term(ctx.session, term_id, ctx.runtime.settings))


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
