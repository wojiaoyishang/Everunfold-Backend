"""Private draft CRUD contract, independent of the published snapshot contract."""

import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.content.component_document import ComponentDocument, ContentEditMode
from app.modules.content.editor_schema import EditorCatalog, EditorSettings, EditorTermCreate
from app.modules.content.management_schemas import (
    DraftBatch,
    TaxonomyCatalog,
    TaxonomyCommand,
    TaxonomyImpact,
    TaxonomyQuery,
    TaxonomyResult,
)
from app.modules.content.publication_schemas import ContentDelivery, ContentSyncState
from app.modules.content.schemas import MediaURL
from app.modules.content.type_registry import ContentTypeDefinition
from app.modules.settings.languages import LanguageCatalog, LanguageCode


def has_upload_placeholder(source: str) -> bool:
    return bool(re.search(r"""<img\b[^>]*\bsrc\s*=\s*["']?everunfold-upload://""", source, re.I))


class DraftBlock(PublicModel):
    id: UUID
    type: Literal["markdown", "html"] = "markdown"
    content: str = Field(max_length=100_000)


class DraftFields(PublicModel):
    title: str = Field(default="", max_length=200)
    excerpt: str = Field(default="", max_length=1000)
    body: list[DraftBlock] = Field(max_length=128)
    edit_mode: ContentEditMode = Field(default="blocks", exclude_if=lambda v: v == "blocks")
    component_document: ComponentDocument | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    settings: EditorSettings | None = None
    access_password: str | None = Field(default=None, min_length=1, max_length=200, repr=False)

    @model_validator(mode="after")
    def bounded_unique_blocks(self) -> Self:
        if self.edit_mode == "components":
            if self.component_document is None:
                raise ValueError("组件模式需要组件文档")
            if has_upload_placeholder(self.component_document.text()):
                raise ValueError("请等待图片上传完成或移除上传占位")
        elif not self.body:
            raise ValueError("区块模式需要正文区块")
        if self.edit_mode == "blocks" and self.component_document is not None:
            raise ValueError("请切换至组件模式后编辑组件文档")
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
    status: Literal["all", "draft", "published", "scheduled", "failed", "trash"] = "all"
    page: int = Field(default=1, ge=1, le=100_000)
    page_size: int = Field(default=15, ge=1, le=50)
    language: LanguageCode | None = None
    grouped: bool = True
    author_id: UUID | None = None
    category: str | None = Field(default=None, max_length=60)
    tag: str | None = Field(default=None, max_length=60)
    updated_from: AwareDatetime | None = None
    updated_to: AwareDatetime | None = None
    order_by: Literal["updated_at", "created_at", "published_at", "title"] = "updated_at"
    direction: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def date_range(self) -> Self:
        if self.updated_from and self.updated_to and self.updated_from > self.updated_to:
            raise ValueError("结束时间不能早于开始时间")
        return self


class DraftLookup(PublicModel):
    id: UUID


class DraftCreate(PublicModel):
    operation_id: UUID
    type_id: str = Field(min_length=1, max_length=64)
    fields: DraftFields
    language: LanguageCode | None = None


class DraftMutation(DraftLookup):
    operation_id: UUID
    expected_revision: int = Field(ge=1)


class DraftSave(DraftMutation):
    fields: DraftFields
    expected_collaboration_seq: int | None = Field(
        default=None, ge=0, exclude_if=lambda v: v is None
    )


class DraftSchedule(DraftSave):
    scheduled_for: AwareDatetime


class DraftSummary(PublicModel):
    edit_mode: ContentEditMode = Field(default="blocks", exclude_if=lambda v: v == "blocks")
    collaboration_seq: int | None = None
    id: UUID
    owner_id: UUID | None = None
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
    scheduled_for: datetime | None = None
    scheduled_revision: int | None = None
    schedule_error: str | None = None
    created_at: datetime
    updated_at: datetime
    language: str = "zh-CN"
    translation_group_id: UUID | None = None


class DraftDetail(DraftSummary):
    component_document: ComponentDocument | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    body: list[DraftBlock]
    settings: EditorSettings = Field(default_factory=EditorSettings)
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)
    has_password: bool = False


class TranslationMember(DraftSummary):
    is_original: bool
    needs_review: bool = False


class DraftListItem(DraftSummary):
    cover_url: str | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    comment_count: int = Field(default=0, ge=0)
    pinned: bool = False
    comments_open: bool = True
    translations: list[TranslationMember] = Field(default_factory=list)
    group_revision: int = 1


class DraftAuthor(PublicModel):
    id: UUID
    name: str


class DraftCatalog(PublicModel):
    items: list[DraftListItem]
    total: int
    page: int
    page_size: int
    authors: list[DraftAuthor] = Field(default_factory=list)


class DraftBatchResult(PublicModel):
    items: list[DraftDetail]


class TranslationCatalog(PublicModel):
    id: UUID
    source_id: UUID
    type_id: str
    revision: int
    members: list[TranslationMember]


class TranslationMutation(PublicModel):
    id: UUID
    operation_id: UUID
    expected_revision: int = Field(ge=1)


class TranslationCreate(TranslationMutation):
    language: LanguageCode
    copy_source: bool = False


class TranslationLink(TranslationMutation):
    target_id: UUID


class TranslationGroupMutation(TranslationMutation):
    members: dict[UUID, int] = Field(min_length=1)


class ContentTypeCatalog(PublicModel):
    items: list[ContentTypeDefinition]


class ContentEditAction(PublicModel):
    id: UUID
    type_id: str


class ContentActions(PublicModel):
    edit: ContentEditAction | None = None


class ContentAdminContract(PublicModel):
    batch: DraftBatch
    batch_result: PublicResponse[DraftBatchResult]
    taxonomy_query: TaxonomyQuery
    taxonomy_catalog: PublicResponse[TaxonomyCatalog]
    taxonomy_command: TaxonomyCommand
    taxonomy_impact: PublicResponse[TaxonomyImpact]
    taxonomy_result: PublicResponse[TaxonomyResult]
    languages: PublicResponse[LanguageCatalog]
    actions: PublicResponse[ContentActions]
    create_category: EditorTermCreate
    editor_catalog: PublicResponse[EditorCatalog]
    query: DraftQuery
    lookup: DraftLookup
    create: DraftCreate
    save: DraftSave
    schedule: DraftSchedule
    mutation: DraftMutation
    catalog: PublicResponse[DraftCatalog]
    detail: PublicResponse[DraftDetail]
    types: PublicResponse[ContentTypeCatalog]
    sync_state: PublicResponse[ContentSyncState]
    delivery: ContentDelivery
    translation_catalog: PublicResponse[TranslationCatalog]
    translation_create: TranslationCreate
    translation_link: TranslationLink
    translation_mutation: TranslationMutation
    translation_group_mutation: TranslationGroupMutation
