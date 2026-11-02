"""HTTP ticket and exact-cutoff commit; first socket frame consumes the one-use ticket."""

from uuid import UUID

from fastapi import APIRouter, Request, WebSocket

from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.auth.router import cookie
from app.modules.auth.security import token_digest
from app.modules.content.collaboration.schemas import CommitDraft, CommittedDraft, TicketState
from app.modules.content.collaboration.service import authorize, commit_draft, ticket
from app.modules.content.collaboration.socket import serve
from app.modules.content.draft_schemas import DraftLookup
from app.modules.settings.router import Authorized

router = APIRouter(
    tags=["collaboration"],
    responses={
        status: {"model": ErrorResponse} for status in (401, 403, 404, 409, 413, 422, 429, 500, 503)
    },
)


@router.post("/v1/admin/content/collaboration/ticket", response_model=PublicResponse[TicketState])
async def issue(payload: DraftLookup, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ticket(ctx.service.session, payload.id, token_digest(cookie(request) or ""))
    )


@router.post(
    "/v1/admin/content/collaboration/commit", response_model=PublicResponse[CommittedDraft]
)
async def commit(payload: CommitDraft, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    actor = await authorize(ctx.service.session, token_digest(cookie(request) or ""), lock=True)
    return JSONResponseSuccess(
        data=await commit_draft(ctx.service.session, ctx.service.config, actor, payload)
    )


@router.websocket("/ws/v1/collab/{draft_id}")
async def collaborate(socket: WebSocket, draft_id: UUID) -> None:
    await serve(socket, draft_id)
