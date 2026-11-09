"""Versioned per-content settings; publishing policies remain separate commands."""

import re
import unicodedata
from typing import Literal
from urllib.parse import unquote
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from app.core.schemas import PublicModel
from app.modules.captcha.schemas import CaptchaOverride


def content_path(value: str) -> str:
    value = unicodedata.normalize("NFC", unquote(value.strip(), errors="strict"))
    if not value:
        return ""
    if (
        not value.startswith("/")
        or value.startswith("//")
        or any(char.isspace() or ord(char) < 32 for char in value)
        or any(char in value for char in "\\?#%<>")
        or any(part in {"", ".", ".."} for part in value[1:].split("/"))
    ):
        raise ValueError("请输入不含查询参数的站内绝对路径")
    first = value.split("/")[1].lower()
    if first in {
        "admin",
        "account",
        "login",
        "api",
        "v1",
        "internal",
        "_next",
        "reference",
        "media",
        "archives",
        "article",
        "categories",
        "category",
        "tag",
        "tags",
        "search",
        "ss",
        "favicon.ico",
        "robots.txt",
        "sitemap.xml",
        "manifest.webmanifest",
    } or first.startswith("."):
        raise ValueError("该路径由系统使用")
    return value


class EditorSettings(PublicModel):
    path: str = Field(default="", max_length=500)
    display_published_at: AwareDatetime | None = None
    cover_media_id: UUID | None = None
    categories: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=30)
    pinned: bool = False
    comments_open: bool = True
    captcha_read: CaptchaOverride = "inherit"
    captcha_comment: CaptchaOverride = "inherit"
    seo_title: str = Field(default="", max_length=200)
    seo_description: str = Field(default="", max_length=500)
    visibility: Literal["public", "password"] = "public"
    opens_at: AwareDatetime | None = None

    @field_validator("path")
    @classmethod
    def normalized_path(cls, value: str) -> str:
        return content_path(value)

    @field_validator("categories", "tags")
    @classmethod
    def normalized_terms(cls, values: list[str]) -> list[str]:
        result: dict[str, str] = {}
        for raw in values:
            value = unicodedata.normalize("NFC", raw.strip())
            if not value or len(value) > 60 or re.search(r"[\x00-\x1f]", value):
                raise ValueError("分类与标签须为 1 至 60 字符")
            result.setdefault(value.casefold(), value)
        return list(result.values())


class EditorTerm(PublicModel):
    name: str
    kind: str
    id: UUID
    count: int = Field(default=0, ge=0)
    parent_id: UUID | None = None


class EditorTermCreate(PublicModel):
    name: str = Field(min_length=1, max_length=60)
    parent_id: UUID | None = None


class EditorCatalog(PublicModel):
    terms: list[EditorTerm]


class PublicEditorSettings(PublicModel):
    comments_open: bool = True
    seo_title: str = ""
    seo_description: str = ""
