"""Versioned request and response models shared by the plugin contract exporter."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.settings.languages import LanguageCode

GlossaryStatus = Literal["draft", "published", "archived"]


class GlossaryFields(PublicModel):
    language: LanguageCode = "zh-CN"
    slug: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    body_markdown: str = Field(default="", max_length=20_000)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    status: GlossaryStatus = "draft"

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        value = value.strip()
        if (
            not value
            or value in {".", ".."}
            or any(char.isspace() or char in "/?#%<>\\" for char in value)
        ):
            raise ValueError("slug 只能包含非空路径片段")
        return value

    @field_validator("title", "summary")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("aliases")
    @classmethod
    def unique_aliases(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(raw.split())
            if not value or len(value) > 80 or any(ord(char) < 32 for char in value):
                raise ValueError("别名为 1 至 80 个可见字符")
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @model_validator(mode="after")
    def archive_has_no_public_requirement(self) -> Self:
        return self


class GlossaryCreate(PublicModel):
    operation_id: UUID
    term_id: UUID
    fields: GlossaryFields


class GlossaryUpdate(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    fields: GlossaryFields


class GlossaryListItem(PublicModel):
    id: UUID
    language: str
    slug: str
    title: str
    summary: str
    aliases: list[str]
    status: GlossaryStatus
    revision: int
    updated_at: datetime
    published_at: datetime | None


class GlossaryDetail(GlossaryListItem):
    body_markdown: str
    created_by: UUID
    updated_by: UUID


class GlossaryList(PublicModel):
    items: list[GlossaryListItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class GlossaryQuery(PublicModel):
    search: str = Field(default="", max_length=200)
    status: Literal["all", "draft", "published", "archived"] = "all"
    language: LanguageCode | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=100)


class PublicGlossaryTerm(PublicModel):
    id: UUID
    language: str
    slug: str
    title: str
    summary: str
    aliases: list[str]
    path: str


class PublicGlossaryIndex(PublicModel):
    revision: str
    terms: list[PublicGlossaryTerm]


class PublicGlossaryDetail(PublicGlossaryTerm):
    body_markdown: str


class PublicGlossaryMatch(PublicGlossaryTerm):
    body_markdown: str
    matched_texts: list[str]


class ContentGlossaryMatches(PublicModel):
    content_id: UUID
    revision: str
    terms: list[PublicGlossaryMatch]


class GlossaryContract(PublicModel):
    public_index: PublicResponse[PublicGlossaryIndex]
    public_detail: PublicResponse[PublicGlossaryDetail]
    content_matches: PublicResponse[ContentGlossaryMatches]
    query: GlossaryQuery
    create: GlossaryCreate
    update: GlossaryUpdate
    list: PublicResponse[GlossaryList]
    detail: PublicResponse[GlossaryDetail]
