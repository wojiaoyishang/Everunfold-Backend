"""Private version, detached branch and three-way merge contract."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue

from app.core.schemas import PublicModel
from app.modules.content.draft_schemas import DraftFields, DraftLookup
from app.modules.content.schemas import MediaURL


class TicketRequest(DraftLookup):
    source_first: bool = False


class VersionQuery(DraftLookup):
    page: int = Field(default=1, ge=1, le=100_000)
    branch_page: int = Field(default=1, ge=1, le=100_000)
    merge_page: int = Field(default=1, ge=1, le=100_000)


class VersionLookup(DraftLookup):
    revision: int = Field(ge=1)


class VersionItem(PublicModel):
    revision: int
    actor: str
    action: str
    note: str
    created_at: AwareDatetime
    epoch: UUID | None
    committed_seq: int | None
    parents: list[int]
    current: bool


class VersionDetail(PublicModel):
    version: VersionItem
    fields: DraftFields
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)


class BranchUpdate(PublicModel):
    update_id: UUID
    payload: str = Field(max_length=1_400_000)


class BranchUpload(DraftLookup):
    branch_id: UUID
    note: str = Field(default="离线草稿", max_length=200)
    base_epoch: UUID | None = None
    base_seq: int | None = Field(default=None, ge=0)
    base_revision: int | None = Field(default=None, ge=1)
    base_branch_id: UUID | None = None
    fields: DraftFields
    updates: list[BranchUpdate] = Field(default_factory=list, max_length=1000)


class BranchLookup(DraftLookup):
    branch_id: UUID


class BranchItem(PublicModel):
    id: UUID
    actor: str
    note: str
    base_revision: int | None
    base_epoch: UUID | None
    base_seq: int | None
    created_at: AwareDatetime
    merged_revision: int | None


class BranchDetail(PublicModel):
    branch: BranchItem
    base: DraftFields
    fields: DraftFields
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)


class MergeConflict(PublicModel):
    key: str
    path: list[str]
    kind: Literal["text", "field", "block", "order"]
    base: JsonValue
    local: JsonValue
    remote: JsonValue


class MergeLock(PublicModel):
    id: UUID
    actor_id: UUID
    actor: str
    target_seq: int
    expires_at: AwareDatetime


class MergeSummary(PublicModel):
    id: UUID
    branch_id: UUID
    owner_id: UUID
    actor: str
    status: Literal["active", "cancelled", "completed", "expired", "discarded"]
    created_at: AwareDatetime
    expires_at: AwareDatetime
    unresolved: int
    result_revision: int | None


class VersionCatalog(PublicModel):
    versions: list[VersionItem]
    branches: list[BranchItem]
    total: int
    branch_total: int
    page: int
    branch_page: int
    page_size: int = 20
    lock: MergeLock | None
    merges: list[MergeSummary]
    merge_total: int
    merge_page: int


class MergeBegin(BranchLookup):
    merge_id: UUID


class MergeLookup(DraftLookup):
    merge_id: UUID


class ConflictResolution(PublicModel):
    key: str
    choice: Literal["local", "remote", "base", "custom"]
    value: JsonValue = None


class MergeResolve(MergeLookup):
    resolutions: list[ConflictResolution] = Field(max_length=1024)
    result: DraftFields | None = None


class MergeDetail(PublicModel):
    id: UUID
    branch_id: UUID
    status: Literal["active", "cancelled", "completed", "expired", "discarded"]
    owner_id: UUID
    epoch: UUID
    target_seq: int
    target_revision: int
    expires_at: AwareDatetime
    base: DraftFields
    local: DraftFields
    remote: DraftFields
    result: DraftFields
    conflicts: list[MergeConflict]
    resolved: list[str]
    result_revision: int | None
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)


class VersionDeleted(PublicModel):
    deleted: Literal[True] = True
