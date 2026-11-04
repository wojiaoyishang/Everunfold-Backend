"""Private draft CRUD contract, independent of the published snapshot contract."""

import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.content.editor_schema import EditorCatalog, EditorSettings, EditorTermCreate
from app.modules.content.publication_schemas import ContentDelivery, ContentSyncState
from app.modules.content.schemas import MediaURL
from app.modules.content.type_registry import ContentTypeDefinition


def has_upload_placeholder(source: str) -> bool:
    return bool(re.search(r"""<img\b[^>]*\bsrc\s*=\s*["']?everunfold-upload://""", source, re.I))


class DraftBlock(PublicModel):
    id: UUID
    type: Literal["markdown", "html"] = "markdown"
    content: str = Field(max_length=100_000)


class DraftFields(PublicModel):
    title: str = Field(default="", max_length=200)
    excerpt: str = Field(default="", max_length=1000)
    body: list[DraftBlock] = Field(min_length=1, max_length=128)
    settings: EditorSettings | None = None
    access_password: str | None = Field(default=None, min_length=1, max_length=200, repr=False)

    @model_validator(mode="after")
    def bounded_unique_blocks(self) -> Self:
        if any(has_upload_placeholder(block.content) for block in self.body):
            raise ValueError("请等待图片上传完成或移除上传占位")
        if len({block.id for block in self.body}) != len(self.body):
            raise ValueError("正文块 ID 不可重复")
        if sum(len(block.content) for block in self.body) > 200_000:
            raise ValueError("正文不能超过 200000 字符")
        return self


class DraftQuery(PublicModel):
    type_id: str = Field(min_length=1, max_length=64)
    search: str = Field(default="", max_length=200)
    status: Literal["all", "draft", "published", "trash"] = "all"
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=15, ge=1, le=50)


class DraftLookup(PublicModel):
    id: UUID


class DraftCreate(PublicModel):
    operation_id: UUID
    type_id: str = Field(min_length=1, max_length=64)
    fields: DraftFields


class DraftMutation(DraftLookup):
    operation_id: UUID
    expected_revision: int = Field(ge=1)


class DraftSave(DraftMutation):
    fields: DraftFields


class DraftSummary(PublicModel):
    id: UUID
    type_id: str
    title: str
    excerpt: str
    preview: str
    author: str
    revision: int
    status: Literal["draft", "trash"]
    is_demo: bool
    published_revision: int | None = None
    public_path: str | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DraftDetail(DraftSummary):
    body: list[DraftBlock]
    settings: EditorSettings = Field(default_factory=EditorSettings)
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)
    has_password: bool = False


class DraftCatalog(PublicModel):
    items: list[DraftSummary]
    total: int
    page: int
    page_size: int


class ContentTypeCatalog(PublicModel):
    items: list[ContentTypeDefinition]


class ContentEditAction(PublicModel):
    id: UUID
    type_id: str


class ContentActions(PublicModel):
    edit: ContentEditAction | None = None


class ContentAdminContract(PublicModel):
    actions: PublicResponse[ContentActions]
    create_category: EditorTermCreate
    editor_catalog: PublicResponse[EditorCatalog]
    query: DraftQuery
    lookup: DraftLookup
    create: DraftCreate
    save: DraftSave
    mutation: DraftMutation
    catalog: PublicResponse[DraftCatalog]
    detail: PublicResponse[DraftDetail]
    types: PublicResponse[ContentTypeCatalog]
    sync_state: PublicResponse[ContentSyncState]
    delivery: ContentDelivery
