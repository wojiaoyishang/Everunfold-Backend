"""Authored source is isolated from the trusted frontend registration manifest."""

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.content.editor_schema import content_path


class PageOption(PublicModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "textarea", "url", "boolean", "select", "list"]
    default: JsonValue = ""
    min_length: int = Field(default=0, ge=0, le=10000)
    choice_labels: dict[str, str] = Field(default_factory=dict)
    max_length: int = Field(default=200, ge=1, le=10000)
    max_items: int = Field(default=100, ge=1, le=300)
    choices: list[str] = Field(default_factory=list, max_length=30)
    fields: list["PageOption"] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def controlled_tree(self) -> Self:
        if self.kind == "list" and (not self.fields or any(f.kind == "list" for f in self.fields)):
            raise ValueError("lists require scalar fields")
        if len({f.key for f in self.fields}) != len(self.fields):
            raise ValueError("duplicate field keys")
        if self.kind != "list" and self.fields:
            raise ValueError("only lists have fields")
        if self.kind == "select" and not self.choices:
            raise ValueError("select requires choices")
        return self


class PageRegistration(PublicModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_.-]{0,63}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    title: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=2, max_length=500)
    description: str = Field(default="", max_length=500)
    fields: list[PageOption] = Field(default_factory=list, max_length=30)
    _path = field_validator("path")(content_path)

    @model_validator(mode="after")
    def distinct_options(self) -> Self:
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError("duplicate option keys")
        return self


class PageManifest(PublicModel):
    protocol: Literal["pages-1"] = "pages-1"
    pages: list[PageRegistration] = Field(max_length=100)

    @model_validator(mode="after")
    def distinct_pages(self) -> Self:
        if len({page.id for page in self.pages}) != len(self.pages):
            raise ValueError("duplicate registered page IDs")
        if len({page.path for page in self.pages}) != len(self.pages):
            raise ValueError("duplicate registered page paths")
        return self


class PageValue(PublicModel):
    title: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=2, max_length=500)
    description: str = Field(default="", max_length=500)
    allow_indexing: bool = True
    enabled: bool = False
    html: str = Field(default="", max_length=200000)
    css: str = Field(default="", max_length=100000)
    javascript: str = Field(default="", max_length=100000)
    javascript_enabled: bool = False
    options: dict[str, JsonValue] = Field(default_factory=dict)
    _path = field_validator("path")(content_path)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class PublicManagedPage(PageValue):
    enabled: Literal[True] = True
    id: UUID
    source: Literal["custom", "registered"]
    implementation_id: str | None = None
    implementation_version: str | None = None
    revision: int = Field(ge=1)


class ManagedPageItem(PublicModel):
    id: UUID
    source: Literal["custom", "registered"]
    implementation_id: str | None
    revision: int
    applied_revision: int | None
    enabled: bool
    value: PageValue
    registration: PageRegistration | None
    available: bool
    updated_at: datetime
    archived: bool


class ManagedPageSummary(PublicModel):
    id: UUID
    source: Literal["custom", "registered"]
    implementation_id: str | None
    title: str
    path: str
    description: str
    revision: int
    applied_revision: int | None
    enabled: bool
    available: bool
    archived: bool
    updated_at: datetime


class ManagedPageCatalog(PublicModel):
    items: list[ManagedPageSummary]


class SaveManagedPage(PublicModel):
    id: UUID
    expected_revision: int = Field(ge=0)
    value: PageValue
    apply: bool = False
    operation_id: UUID
    current_password: str = Field(default="", max_length=1024)


class RemoveManagedPage(PublicModel):
    restore: bool = False
    id: UUID
    expected_revision: int = Field(ge=1)
    operation_id: UUID


class PagesContract(PublicModel):
    catalog: PublicResponse[ManagedPageCatalog]
    item: PublicResponse[ManagedPageItem]
    save: SaveManagedPage
    remove: RemoveManagedPage
    manifest: PageManifest
    public_page: PublicManagedPage
