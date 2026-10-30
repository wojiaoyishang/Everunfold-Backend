"""Public DTOs. Fields are deliberately separate from private persistence models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ContentKind = Literal["article", "page"]
TaxonomyKind = Literal["category", "tag"]


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicResponse[T](PublicModel):
    success: bool = True
    code: int = 200
    msg: str = "请求成功"
    data: T


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


class ContentSummary(PublicModel):
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


class PublicComment(PublicModel):
    id: str
    parent_id: str | None
    author: str
    content: str
    created_at: datetime
    reply_to: str | None = None


class TaxonomyList(PublicModel):
    items: list[TaxonomyDTO]


class PublicSnapshot(PublicModel):
    schema_version: Literal["0.1.0"] = "0.1.0"
    source_epoch: str = Field(min_length=1)
    generation: int = Field(ge=1)
    exported_at: datetime
    site: SiteDTO
    contents: list[ContentDetail]
    comments: dict[str, list[PublicComment]]
    taxonomies: list[TaxonomyDTO]
