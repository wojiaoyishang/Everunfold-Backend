"""Private HTTP and socket protocol version 1; credentials never enter URLs."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.core.schemas import PublicModel, PublicResponse
from app.modules.content.draft_schemas import DraftDetail, DraftLookup, DraftMutation


class TicketState(PublicModel):
    ticket: str = Field(repr=False)
    draft_id: UUID
    epoch: UUID
    ws_path: str
    protocol: Literal["collab-1"] = "collab-1"


class CommitDraft(DraftMutation):
    epoch: UUID
    committed_seq: int = Field(ge=0)
    action: Literal["save", "publish", "withdraw"] = "save"
    visibility: Literal["public", "password"] = "public"
    opens_at: AwareDatetime | None = None
    access_password: str | None = Field(default=None, min_length=1, max_length=200, repr=False)


class CommittedDraft(PublicModel):
    draft: DraftDetail
    committed_seq: int


class ClientUpdate(PublicModel):
    type: Literal["update"] = "update"
    epoch: UUID
    update_id: UUID
    payload: str = Field(max_length=1_400_000)


class ClientAuth(PublicModel):
    type: Literal["auth"] = "auth"
    ticket: str = Field(max_length=128, repr=False)
    client_id: int = Field(ge=0, le=4_294_967_295)
    state_vector: str = Field(max_length=16_384)


class Cursor(PublicModel):
    anchor: str = Field(max_length=512)
    head: str = Field(max_length=512)


class PresenceInput(PublicModel):
    type: Literal["presence"] = "presence"
    block_id: UUID | None = None
    cursor: Cursor | None = None


class Peer(PublicModel):
    connection_id: UUID
    client_id: int = Field(ge=0, le=4_294_967_295)
    name: str
    color: str
    block_id: UUID | None = None
    cursor: Cursor | None = None


class ServerFrame(PublicModel):
    type: Literal["sync", "update", "ack", "peers", "error", "revision"]
    epoch: UUID | None = None
    committed_seq: int | None = None
    payload: str | None = None
    update_id: UUID | None = None
    peers: list[Peer] | None = None
    revision: int | None = None
    code: int | None = None
    msg: str | None = None


class CollaborationContract(PublicModel):
    lookup: DraftLookup
    ticket: PublicResponse[TicketState]
    commit: CommitDraft
    committed: PublicResponse[CommittedDraft]
    update: ClientUpdate
    presence: PresenceInput
    frame: ServerFrame
    auth: ClientAuth
