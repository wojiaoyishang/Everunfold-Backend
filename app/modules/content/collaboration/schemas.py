"""Private HTTP and socket protocol version 1; credentials never enter URLs."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.content.draft_schemas import (
    DraftDetail,
    DraftLookup,
    DraftMutation,
    has_upload_placeholder,
)
from app.modules.content.schemas import MediaURL
from app.modules.content.version_schemas import (
    BranchDetail,
    BranchLookup,
    BranchUpload,
    MergeBegin,
    MergeDetail,
    MergeLock,
    MergeLookup,
    MergeResolve,
    TicketRequest,
    VersionCatalog,
    VersionDeleted,
    VersionDetail,
    VersionLookup,
    VersionQuery,
)


class TicketState(PublicModel):
    ticket: str = Field(repr=False)
    draft_id: UUID
    epoch: UUID
    ws_path: str
    protocol: Literal["collab-1", "collab-2"] = "collab-1"


class BlockSourcePatch(PublicModel):
    id: UUID
    type: Literal["markdown", "html"]
    base_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = Field(max_length=100_000)

    @field_validator("content")
    @classmethod
    def uploaded_images_ready(cls, value: str) -> str:
        if has_upload_placeholder(value):
            raise ValueError("请等待图片上传完成或移除上传占位")
        return value


class CommitDraft(DraftMutation):
    epoch: UUID
    committed_seq: int = Field(ge=0)
    action: Literal["save", "publish", "withdraw"] = "save"
    note: str = Field(default="", max_length=200)
    block_sources: list[BlockSourcePatch] = Field(default_factory=list, max_length=128)
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
    rich_versions: dict[UUID, int] = Field(default_factory=dict, max_length=512)


class SourceAcquire(DraftLookup):
    block_id: UUID
    client_id: int = Field(ge=0, le=4_294_967_295)


class SourceCommand(DraftLookup):
    block_id: UUID
    lease_id: UUID


class SourceApply(SourceCommand):
    operation_id: UUID
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    html: str = Field(max_length=100_000)


class SourceState(PublicModel):
    block_id: UUID
    generation: int = Field(ge=0)
    client_id: int | None
    expires_at: AwareDatetime | None


class SourceAcquired(PublicModel):
    lease_id: UUID
    html: str
    expected_hash: str
    expires_at: AwareDatetime


class SourceApplied(PublicModel):
    operation_id: UUID
    committed_seq: int
    payload: str
    generation: int


class SourceReleased(PublicModel):
    released: Literal[True] = True


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
    type: Literal[
        "sync",
        "update",
        "ack",
        "peers",
        "error",
        "revision",
        "media",
        "sources",
        "control",
        "reset",
    ]
    epoch: UUID | None = None
    committed_seq: int | None = None
    payload: str | None = None
    update_id: UUID | None = None
    peers: list[Peer] | None = None
    revision: int | None = None
    code: int | None = None
    msg: str | None = None
    media_urls: dict[str, MediaURL] | None = None
    sources: list[SourceState] | None = None
    merge: MergeLock | None = None


class CollaborationContract(PublicModel):
    source_acquire: SourceAcquire
    source_command: SourceCommand
    source_apply: SourceApply
    source_acquired: PublicResponse[SourceAcquired]
    source_applied: PublicResponse[SourceApplied]
    source_released: PublicResponse[SourceReleased]
    lookup: TicketRequest
    ticket: PublicResponse[TicketState]
    commit: CommitDraft
    committed: PublicResponse[CommittedDraft]
    update: ClientUpdate
    presence: PresenceInput
    frame: ServerFrame
    auth: ClientAuth
    version_query: VersionQuery
    version_lookup: VersionLookup
    version_catalog: PublicResponse[VersionCatalog]
    version_detail: PublicResponse[VersionDetail]
    version_deleted: PublicResponse[VersionDeleted]
    branch_upload: BranchUpload
    branch_lookup: BranchLookup
    branch_detail: PublicResponse[BranchDetail]
    merge_begin: MergeBegin
    merge_lookup: MergeLookup
    merge_resolve: MergeResolve
    merge_detail: PublicResponse[MergeDetail]
