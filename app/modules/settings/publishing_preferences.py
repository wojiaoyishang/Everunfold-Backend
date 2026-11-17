"""Site-wide reading and first-save defaults."""

import re
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.core.schemas import PublicModel
from app.modules.content.text_preview import DEFAULT_EXCERPT_LENGTH

DEFAULT_PERMALINK = "/%postname%.html"
PERMALINK_SAMPLE = {
    "%postname%": "sample-article",
    "%post_id%": "00000000-0000-0000-0000-000000000001",
    "%year%": "2026",
    "%monthnum%": "01",
    "%day%": "01",
}


class ReadingPreferences(PublicModel):
    time_zone: str = Field(default="Asia/Shanghai", max_length=64)
    date_format: Literal["ymd-slash", "ymd-dash", "localized", "dmy", "mdy"] = "ymd-slash"
    home_page_id: UUID | None = None
    posts_page_id: UUID | None = None

    @field_validator("time_zone")
    @classmethod
    def valid_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("请选择有效的 IANA 时区") from exc
        return value

    @model_validator(mode="after")
    def distinct_pages(self) -> "ReadingPreferences":
        if self.home_page_id and self.home_page_id == self.posts_page_id:
            raise ValueError("静态首页与文章列表页须使用不同的页面")
        return self


class WritingPreferences(PublicModel):
    default_category_id: UUID | None = None
    comments_open: bool = True
    permalink: str = Field(default=DEFAULT_PERMALINK, min_length=3, max_length=160)
    excerpt_length: int = Field(
        default=DEFAULT_EXCERPT_LENGTH,
        ge=1,
        le=1000,
        exclude_if=lambda value: value == DEFAULT_EXCERPT_LENGTH,
    )
    default_excerpt: str = Field(default="", max_length=1000, exclude_if=lambda value: not value)

    @field_validator("default_excerpt", mode="before")
    @classmethod
    def trim_excerpt(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("permalink")
    @classmethod
    def valid_permalink(cls, value: str) -> str:
        from app.modules.content.editor_schema import content_path

        if "%postname%" not in value and "%post_id%" not in value:
            raise ValueError("固定链接须包含 %postname% 或 %post_id%")
        if not re.fullmatch(r"/[a-zA-Z0-9_./%-]+", value):
            raise ValueError("固定链接模板须为站内路径")
        sample = re.sub(r"%[a-z_]+%", lambda match: PERMALINK_SAMPLE.get(match[0], match[0]), value)
        if "%" in sample:
            raise ValueError("固定链接包含未知变量")
        content_path(sample)
        return value
