"""Sparse translation overrides; immutable built-in dictionaries remain code assets."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.core.schemas import PublicModel, PublicResponse

TranslationScope = Literal["public", "admin", "backend"]


class TranslationModel(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TranslationOverrides(TranslationModel):
    public: dict[str, dict[str, str]] = Field(default_factory=dict)
    admin: dict[str, dict[str, str]] = Field(default_factory=dict)
    backend: dict[str, dict[str, str]] = Field(default_factory=dict)


class TranslationCatalogEntry(TranslationModel):
    scope: TranslationScope
    key: str = Field(min_length=1, max_length=512)
    owner: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=4000)
    defaults: dict[str, str] = Field(default_factory=dict)


class TranslationOverrideItem(TranslationModel):
    id: UUID
    scope: TranslationScope
    key: str
    owner: str
    locale: str
    source: str
    source_hash: str
    value: str
    revision: int
    updated_at: datetime


class TranslationState(TranslationModel):
    revision: int = Field(ge=1)
    overrides: list[TranslationOverrideItem]
    backend_catalog: list[TranslationCatalogEntry]


class TranslationChange(TranslationModel):
    scope: TranslationScope
    key: str = Field(min_length=1, max_length=512)
    locale: str = Field(min_length=2, max_length=32)
    source: str = Field(min_length=1, max_length=4000)
    value: str | None = Field(default=None, max_length=4000)


class SaveTranslations(TranslationModel):
    expected_revision: int = Field(ge=1)
    changes: list[TranslationChange] = Field(max_length=500)


class TranslationCatalog(TranslationModel):
    revision: int = Field(ge=1)
    items: list[TranslationCatalogEntry]


translations_state = PublicResponse[TranslationState]
translations_catalog = PublicResponse[TranslationCatalog]
