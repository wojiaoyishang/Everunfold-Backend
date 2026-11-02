"""HTTP ticket and exact-cutoff commit; first socket frame consumes the one-use ticket."""

from uuid import UUID

from fastapi import APIRouter, Request, WebSocket

from app.core.responses import JSONResponseSuccess
from app.core.schemas import PublicResponse
from app.modules.auth.router import cookie
from app.modules.auth.security import token_digest
from app.modules.content.collaboration.schemas import CommitDraft, CommittedDraft, TicketState
from app.modules.content.collaboration.service import commit_draft, ticket
from app.modules.content.collaboration.socket import serve
from app.modules.content.draft_schemas import DraftLookup
from app.modules.settings.router import Authorized

router = APIRouter(tags=["collaboration"])


@router.post("/v1/admin/content/collaboration/ticket", response_model=PublicResponse[TicketState])
async def issue(payload: DraftLookup, request: Request, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await ticket(ctx.service.session, payload.id, token_digest(cookie(request) or ""))
    )


@router.post(
    "/v1/admin/content/collaboration/commit", response_model=PublicResponse[CommittedDraft]
)
async def commit(payload: CommitDraft, ctx: Authorized) -> JSONResponseSuccess:
    return JSONResponseSuccess(
        data=await commit_draft(ctx.service.session, ctx.service.config, ctx.actor, payload)
    )


@router.websocket("/ws/v1/collab/{draft_id}")
async def collaborate(socket: WebSocket, draft_id: UUID) -> None:
    await serve(socket, draft_id)
