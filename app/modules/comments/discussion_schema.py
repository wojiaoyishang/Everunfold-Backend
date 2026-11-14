"""Site-wide discussion controls. Only the guest admission flag is public."""

import unicodedata
from typing import Annotated

from pydantic import Field, field_validator

from app.core.schemas import PublicModel

Keyword = Annotated[str, Field(max_length=100)]


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


class DiscussionPolicy(PublicModel):
    allow_guest_comments: bool


class DiscussionSettings(DiscussionPolicy):
    revision: int = Field(ge=1)
    auto_approve_previously_approved: bool
    moderation_link_threshold: int = Field(ge=0, le=50)
    moderation_keywords: list[Keyword] = Field(max_length=100)
    blocked_keywords: list[Keyword] = Field(max_length=100)

    @field_validator("moderation_keywords", "blocked_keywords")
    @classmethod
    def clean_keywords(cls, values: list[str]) -> list[str]:
        keywords: dict[str, str] = {}
        for value in values:
            value = value.strip()
            if value:
                keywords.setdefault(normalized(value), value)
        return list(keywords.values())
