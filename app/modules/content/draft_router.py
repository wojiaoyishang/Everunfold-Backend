"""Private content management, assembled only through content.router_init."""

from uuid import UUID

from fastapi import APIRouter

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.content.draft_repository import DraftRepository
from app.modules.content.draft_schemas import (
    ContentActions,
    ContentTypeCatalog,
    DraftCatalog,
    DraftCreate,
    DraftDetail,
    DraftLookup,
    DraftMutation,
    DraftQuery,
    DraftSave,
    DraftSchedule,
)
from app.modules.content.draft_service import DraftService, require_type
from app.modules.content.editor_schema import EditorCatalog, EditorTermCreate
from app.modules.content.editor_service import create_category, editor_catalog
from app.modules.content.publication import ContentPublication
from app.modules.content.publication_schemas import ContentSyncState
from app.modules.content.type_registry import content_types
from app.modules.settings.languages import LanguageCatalog, language_catalog
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/content",
    tags=["content-management"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)},
)


@router.get("/types", response_model=PublicResponse[ContentTypeCatalog])
async def types(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=ContentTypeCatalog(items=list(content_types.all())))


@router.get("/languages", response_model=PublicResponse[LanguageCatalog])
async def languages(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await language_catalog(ctx.service.session))


@router.get("/editor-options", response_model=PublicResponse[EditorCatalog])
async def editor_options(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await editor_catalog(ctx.service.session))


@router.get("/actions", response_model=PublicResponse[ContentActions])
async def content_actions(id: UUID, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(data=await DraftService(ctx.service.session).public_actions(id))


@router.post("/categories", response_model=PublicResponse[EditorCatalog])
async def new_category(payload: EditorTermCreate, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await create_category(ctx.service.session, ctx.actor.id, payload)
    )


@router.post("/query", response_model=PublicResponse[DraftCatalog])
async def query(payload: DraftQuery, ctx: Authorized) -> JSONResponseSuccess:
    require_type(payload.type_id)
    return JSONResponseSuccess(
        data=await DraftRepository(
            ctx.service.session,
            ctx.service.config,
            owner_id=ctx.actor.id if ctx.actor.permissions.get("content:read") == "own" else None,
        ).catalog(payload)
    )


@router.post("/detail", response_model=PublicResponse[DraftDetail])
async def read(payload: DraftLookup, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).read(payload.id)
    )


@router.post("/create", response_model=PublicResponse[DraftDetail])
async def create(payload: DraftCreate, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "create", payload, ctx.actor
        )
    )


@router.post("/save", response_model=PublicResponse[DraftDetail])
async def save(payload: DraftSave, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "save", payload, ctx.actor
        )
    )


@router.post("/trash", response_model=PublicResponse[DraftDetail])
async def trash(payload: DraftMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "trash", payload, ctx.actor
        )
    )


@router.post("/restore", response_model=PublicResponse[DraftDetail])
async def restore(payload: DraftMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "restore", payload, ctx.actor
        )
    )


@router.post("/publish", response_model=PublicResponse[DraftDetail])
async def publish(payload: DraftSave, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "publish", payload, ctx.actor
        )
    )


@router.post("/withdraw", response_model=PublicResponse[DraftDetail])
async def withdraw(payload: DraftMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "withdraw", payload, ctx.actor
        )
    )


@router.post("/schedule", response_model=PublicResponse[DraftDetail])
async def schedule(payload: DraftSchedule, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "schedule", payload, ctx.actor
        )
    )


@router.post("/cancel-schedule", response_model=PublicResponse[DraftDetail])
async def cancel_schedule(payload: DraftMutation, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "cancel_schedule", payload, ctx.actor
        )
    )


@router.get("/publication", response_model=PublicResponse[ContentSyncState])
async def publication(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ContentPublication(ctx.service.session, ctx.service.config).state()
    )


@router.post("/sync", response_model=PublicResponse[ContentSyncState])
async def sync(ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ContentPublication(ctx.service.session, ctx.service.config).retry(ctx.actor.id)
    )


@router.post(
    "/components/create",
    operation_id="createComponentDraft",
    response_model=PublicResponse[DraftDetail],
)
async def create_components(payload: DraftCreate, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "create", payload, ctx.actor, component_editor=True
        )
    )


@router.post(
    "/components/save",
    operation_id="saveComponentDraft",
    response_model=PublicResponse[DraftDetail],
)
async def save_components(payload: DraftSave, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "save", payload, ctx.actor, component_editor=True
        )
    )


@router.post(
    "/components/publish",
    operation_id="publishComponentDraft",
    response_model=PublicResponse[DraftDetail],
)
async def publish_components(payload: DraftSave, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "publish", payload, ctx.actor, component_editor=True
        )
    )


@router.post(
    "/components/schedule",
    operation_id="scheduleComponentDraft",
    response_model=PublicResponse[DraftDetail],
)
async def schedule_components(payload: DraftSchedule, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await DraftService(ctx.service.session, ctx.service.config).mutate(
            "schedule", payload, ctx.actor, component_editor=True
        )
    )
