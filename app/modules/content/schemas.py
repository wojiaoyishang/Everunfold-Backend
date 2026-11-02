"""Public DTOs. Fields are deliberately separate from private persistence models."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.core.schemas import ErrorResponse as ErrorResponse
from app.core.schemas import PublicModel as PublicModel
from app.core.schemas import PublicResponse as PublicResponse
from app.modules.content.editor_schema import PublicEditorSettings

ContentKind = Literal["article", "shuoshuo", "page"]
TaxonomyKind = Literal["category", "tag"]


class Page[T](PublicModel):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class TaxonomyDTO(PublicModel):
    id: str
    slug: str
    name: str
    kind: TaxonomyKind
    count: int = Field(ge=0)


class NavigationItem(PublicModel):
    label: str
    href: str
    icon: str = ""


class SiteStats(PublicModel):
    articles: int = Field(ge=0)
    pages: int = Field(ge=0)
    comments: int = Field(ge=0)
    tags: int = Field(ge=0)
    categories: int = Field(ge=0)


class SiteDTO(PublicModel):
    name: str
    description: str
    author: str
    avatar_url: str | None
    hero_url: str | None
    hero_title: str
    hero_subtitle: str
    notice: str
    navigation: list[NavigationItem]
    stats: SiteStats


class ContentSummary(PublicEditorSettings):
    access: Literal["public", "password", "scheduled"] = "public"
    opens_at: datetime | None = None
    id: str
    kind: ContentKind
    title: str
    path: str = Field(pattern="^/")
    excerpt: str
    language: str
    published_at: datetime
    updated_at: datetime
    author: str
    cover_url: str | None
    reading_minutes: int = Field(ge=1)
    word_count: int = Field(ge=0)
    categories: list[TaxonomyDTO]
    tags: list[TaxonomyDTO]
    comment_count: int = Field(ge=0)
    pinned: bool


class ContentBlock(PublicModel):
    id: str = Field(min_length=1)
    type: Literal["markdown", "html"]
    content: str


class MediaURL(PublicModel):
    original: str
    webp: str = ""


class ContentDetail(ContentSummary):
    content: list[ContentBlock]
    media_urls: dict[str, MediaURL]

    @model_validator(mode="after")
    def no_restricted_payload(self) -> Self:
        if self.access != "public" and (
            self.content
            or self.media_urls
            or self.excerpt
            or self.cover_url
            or self.seo_title
            or self.seo_description
            or self.word_count
            or self.comment_count
        ):
            raise ValueError("Restricted snapshots may contain only the public shell")
        if self.access == "scheduled" and not self.opens_at:
            raise ValueError("Scheduled content requires an opening time")
        return self


class PublicComment(PublicModel):
    id: str
    parent_id: str | None
    author: str
    content: str = Field(description="纯文本，按文本展示，不再作为 HTML 解析")
    created_at: datetime
    reply_to: str | None = None


class TaxonomyList(PublicModel):
    items: list[TaxonomyDTO]


class PublicSnapshot(PublicModel):
    schema_version: Literal["0.1.0", "0.2.0", "0.3.0", "0.4.0"] = "0.4.0"
    source_epoch: str = Field(min_length=1)
    generation: int = Field(ge=1)
    exported_at: datetime
    site: SiteDTO
    contents: list[ContentDetail]
    comments: dict[str, list[PublicComment]]
    taxonomies: list[TaxonomyDTO]
