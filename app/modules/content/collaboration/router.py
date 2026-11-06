"""HTTP ticket and exact-cutoff commit; first socket frame consumes the one-use ticket."""

from uuid import UUID

from fastapi import APIRouter, Request, WebSocket

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import cookie
from app.modules.auth.security import token_digest
from app.modules.content.collaboration.schemas import (
    CommitDraft,
    CommittedDraft,
    SourceAcquire,
    SourceAcquired,
    SourceApplied,
    SourceApply,
    SourceCommand,
    SourceReleased,
    TicketState,
)
from app.modules.content.collaboration.service import authorize, commit_draft, ticket
from app.modules.content.collaboration.socket import serve
from app.modules.content.collaboration.source_leases import (
    acquire_source,
    apply_source,
    command_source,
)
from app.modules.content.version_schemas import TicketRequest
from app.modules.settings.router import Authorized

router = APIRouter(
    tags=["collaboration"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 422, 429, 500, 503)
    },
)


@router.post(
    "/v1/admin/content/collaboration/source/acquire", response_model=PublicResponse[SourceAcquired]
)
async def source_acquire(
    payload: SourceAcquire, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await acquire_source(
            ctx.service.session,
            payload,
            ctx.actor.credential_hash or token_digest(cookie(request) or ""),
        )
    )


@router.post(
    "/v1/admin/content/collaboration/source/renew", response_model=PublicResponse[SourceAcquired]
)
async def source_renew(
    payload: SourceCommand, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await command_source(
            ctx.service.session,
            payload,
            ctx.actor.credential_hash or token_digest(cookie(request) or ""),
            release=False,
        )
    )


@router.post(
    "/v1/admin/content/collaboration/source/release", response_model=PublicResponse[SourceReleased]
)
async def source_release(
    payload: SourceCommand, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    await command_source(
        ctx.service.session,
        payload,
        ctx.actor.credential_hash or token_digest(cookie(request) or ""),
        release=True,
    )
    return JSONResponseSuccess(data=SourceReleased())


@router.post(
    "/v1/admin/content/collaboration/source/apply", response_model=PublicResponse[SourceApplied]
)
async def source_apply(
    payload: SourceApply, request: Request, ctx: Authorized
) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await apply_source(
            ctx.service.session,
            ctx.service.config,
            payload,
            ctx.actor.credential_hash or token_digest(cookie(request) or ""),
        )
    )


@router.post("/v1/admin/content/collaboration/ticket", response_model=PublicResponse[TicketState])
async def issue(payload: TicketRequest, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ticket(
            ctx.service.session,
            payload.id,
            ctx.actor.credential_hash or token_digest(cookie(request) or ""),
            source_first=payload.source_first,
        )
    )


@router.post(
    "/v1/admin/content/collaboration/commit", response_model=PublicResponse[CommittedDraft]
)
async def commit(payload: CommitDraft, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    actor = await authorize(
        ctx.service.session,
        ctx.actor.credential_hash or token_digest(cookie(request) or ""),
        lock=True,
        draft_id=payload.id,
        capability="content:publish"
        if payload.action in {"publish", "withdraw"}
        else "content:write",
    )
    return JSONResponseSuccess(
        data=await commit_draft(ctx.service.session, ctx.service.config, actor, payload)
    )


@router.websocket("/ws/v1/collab/{draft_id}")
async def collaborate(socket: WebSocket, draft_id: UUID) -> None:
    await serve(socket, draft_id)
