"""Private operational contracts; no visitor fingerprint or token leaves the service."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import PublicModel, PublicResponse

CommentStatus = Literal["pending", "approved", "rejected", "spam", "trash", "hidden"]


class CommentQuery(PublicModel):
    status: CommentStatus | None = None
    search: str = Field(default="", max_length=200)
    content_id: UUID | None = None
    comment_id: UUID | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ModerationItem(PublicModel):
    id: UUID
    revision: int
    author: str
    body: str
    status: CommentStatus
    created_at: datetime
    updated_at: datetime | None
    parent_id: UUID | None
    reply_to: str | None
    content_id: UUID
    content_type: Literal["article", "shuoshuo", "page"]
    content_title: str
    content_path: str | None


class ModerationCatalog(PublicModel):
    items: list[ModerationItem]
    total: int
    counts: dict[str, int]
    page: int
    page_size: int


class CommentVersion(PublicModel):
    id: UUID
    revision: int = Field(ge=1)


class ModerateComments(PublicModel):
    operation_id: UUID
    items: list[CommentVersion] = Field(min_length=1, max_length=100)
    action: Literal["approve", "pending", "reject", "spam", "trash", "restore"]
    # Omit the default so receipts created before this field still match retries.
    pending_only: bool = Field(default=False, exclude_if=lambda value: not value)

    @field_validator("items")
    @classmethod
    def distinct(cls, value: list[CommentVersion]) -> list[CommentVersion]:
        if len({item.id for item in value}) != len(value):
            raise ValueError("duplicate comment IDs")
        return value


class EditComment(CommentVersion):
    operation_id: UUID
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comment must not be blank")
        return value.strip()


class CommentResult(PublicModel):
    items: list[ModerationItem]


class WorkspaceItem(PublicModel):
    id: UUID
    title: str
    href: str
    updated_at: datetime
    scheduled_for: datetime | None = None
    error: str | None = None


class WorkspaceSummary(PublicModel):
    pending_comments: int | None = None
    drafts: int | None = None
    scheduled: int | None = None
    failed_schedules: int | None = None
    translations_to_review: int | None = None
    failed_deliveries: int | None = None
    recent: list[WorkspaceItem] = Field(default_factory=list)
    upcoming: list[WorkspaceItem] = Field(default_factory=list)


class OperationsContract(PublicModel):
    query: CommentQuery
    moderate: ModerateComments
    edit: EditComment
    catalog: PublicResponse[ModerationCatalog]
    result: PublicResponse[CommentResult]
    workspace: PublicResponse[WorkspaceSummary]
