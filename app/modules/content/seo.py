"""Shared typed metadata overrides for authored public routes."""

import unicodedata
from typing import Literal
from urllib.parse import unquote
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import PublicModel


class SeoFields(PublicModel):
    allow_indexing: bool = True
    seo_title: str = Field(default="", max_length=200, exclude_if=lambda value: not value)
    seo_description: str = Field(default="", max_length=500, exclude_if=lambda value: not value)
    seo_canonical_path: str = Field(default="", max_length=500, exclude_if=lambda value: not value)
    seo_share_title: str = Field(default="", max_length=200, exclude_if=lambda value: not value)
    seo_share_description: str = Field(
        default="", max_length=500, exclude_if=lambda value: not value
    )
    seo_image_alt: str = Field(default="", max_length=500, exclude_if=lambda value: not value)
    seo_max_snippet: int | None = Field(
        default=None, ge=0, le=5000, exclude_if=lambda value: value is None
    )
    seo_image_preview: Literal["none", "standard", "large"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    seo_structured_data: Literal[False] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @field_validator("seo_canonical_path")
    @classmethod
    def canonical_path(cls, value: str) -> str:
        value = unicodedata.normalize("NFC", unquote(value.strip(), errors="strict"))
        if not value or value == "/":
            return value
        if (
            not value.startswith("/")
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in "\\?#%<>")
            or any(part in {"", ".", ".."} for part in value.removesuffix("/")[1:].split("/"))
        ):
            raise ValueError("规范网址须为不含查询参数或锚点的站内路径")
        return value

    def seo_overrides(self) -> dict[str, object]:
        return {
            key: value
            for key, value in self.model_dump(mode="json").items()
            if key in SeoFields.model_fields and value != SeoFields.model_fields[key].default
        }

    def clear_seo(self) -> None:
        for name, field in SeoFields.model_fields.items():
            setattr(self, name, False if name == "allow_indexing" else field.default)


class GlossaryDiscoveryEntry(PublicModel):
    id: UUID
    language: str
    slug: str
    updated_at: str
    allow_indexing: bool = True
    seo_canonical_path: str = ""
