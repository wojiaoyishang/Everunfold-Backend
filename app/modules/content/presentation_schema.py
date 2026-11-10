"""Draft 0.2 presentation contract; not an installed HTTP API or snapshot format.

The frontend uses generated types for the public reading prototype. Existing
0.1 DTOs, persistence and routes remain unchanged until the migration is ready.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from app.modules.content.schemas import (
    ContentBlock,
    ContentTranslation,
    MediaURL,
    PublicModel,
    TaxonomyDTO,
)


class ContentPresentation(PublicModel):
    id: str = Field(min_length=1)
    type: Literal["article", "shuoshuo", "page"]
    title: str
    path: str = Field(pattern="^/")
    excerpt: str
    language: str
    translation_group_id: str | None = None
    translations: list[ContentTranslation] = Field(default_factory=list)
    published_at: datetime
    updated_at: datetime
    author: str
    cover_url: str | None
    reading_minutes: int = Field(ge=0)
    word_count: int = Field(ge=0)
    categories: list[TaxonomyDTO]
    tags: list[TaxonomyDTO]
    comment_count: int = Field(ge=0)
    pinned: bool
    content: list[ContentBlock]
    media_urls: dict[str, MediaURL]
    comments_open: bool
    show_glossary_tooltips: bool = True
    show_glossary_footnotes: bool = True
    allow_indexing: bool = True
    seo_title: str = ""
    seo_description: str = ""
    reaction_count: int = Field(ge=0)
    access: Literal["public", "password", "scheduled", "captcha"]
    opens_at: datetime | None

    @model_validator(mode="after")
    def validate_public_projection(self) -> Self:
        if self.type != "shuoshuo" and not self.title.strip():
            raise ValueError("Only shuoshuo may omit its display title")
        if self.access != "public" and (self.content or self.media_urls or self.excerpt):
            raise ValueError("Locked projections must not contain body, media or excerpts")
        if self.access == "scheduled" and self.opens_at is None:
            raise ValueError("Scheduled projections require an opening time")
        return self
