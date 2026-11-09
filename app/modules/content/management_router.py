"""List operations and term management under the existing explicit authorization registry."""

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.content.draft_schemas import DraftBatchResult
from app.modules.content.management_schemas import (
    DraftBatch,
    TaxonomyCatalog,
    TaxonomyCommand,
    TaxonomyImpact,
    TaxonomyQuery,
    TaxonomyResult,
)
from app.modules.content.management_service import mutate_batch
from app.modules.content.taxonomy_service import TaxonomyService
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/content",
    tags=["content-management"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)


@router.post("/batch", response_model=PublicResponse[DraftBatchResult])
async def batch(payload: DraftBatch, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await mutate_batch(ctx.service.session, ctx.service.config, ctx.actor, payload)
    )


@router.post("/taxonomy/query", response_model=PublicResponse[TaxonomyCatalog])
async def taxonomy_query(payload: TaxonomyQuery, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await TaxonomyService(ctx.service.session, ctx.service.config).catalog(payload)
    )


@router.post("/taxonomy/preview", response_model=PublicResponse[TaxonomyImpact])
async def taxonomy_preview(payload: TaxonomyCommand, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await TaxonomyService(ctx.service.session, ctx.service.config).preview(payload)
    )


@router.post("/taxonomy/save", response_model=PublicResponse[TaxonomyResult])
async def taxonomy_save(payload: TaxonomyCommand, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await TaxonomyService(ctx.service.session, ctx.service.config).mutate(
            ctx.actor, payload
        )
    )
