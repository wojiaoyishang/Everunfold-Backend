"""Authored source is isolated from the trusted frontend registration manifest."""

import json
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.captcha.schemas import CaptchaOverride
from app.modules.content.component_document import ComponentDocument, PageEditMode, PageLayout
from app.modules.content.editor_schema import content_path
from app.modules.content.seo import SeoFields
from app.modules.media.schemas import MediaURL
from app.modules.pages.options import bounded_options
from app.modules.settings.languages import LanguageCode

CUSTOM_PAGE_TYPE = "core.custom"
PAGE_TYPE_PATTERN = r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_.-]{0,63}$"
TEMPLATE_TYPE_PREFIX = "template.t-"


def template_type_id(identity: UUID) -> str:
    return f"{TEMPLATE_TYPE_PREFIX}{identity.hex}"


class PageOption(PublicModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    label: str = Field(min_length=1, max_length=80)
    kind: Literal["text", "textarea", "url", "boolean", "select", "list"]
    default: JsonValue = Field(default_factory=str)
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
    id: str = Field(pattern=PAGE_TYPE_PATTERN, max_length=100)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    title: str = Field(min_length=1, max_length=120)
    path: str | None = Field(
        default=None, min_length=2, max_length=500, description="Suggested path for a new page."
    )
    description: str = Field(default="", max_length=500)
    comments_open: bool = True
    seo_editable: bool = Field(default=True, exclude_if=lambda value: value)
    fields: list[PageOption] = Field(default_factory=list, max_length=30)
    defaults: dict[str, JsonValue] = Field(default_factory=dict)
    _defaults = field_validator("defaults")(bounded_options)
    settings_schema: dict[str, JsonValue] = Field(default_factory=dict)
    _settings_schema = field_validator("settings_schema")(bounded_options)

    def initial_options(self) -> dict[str, JsonValue]:
        return {field.key: field.default for field in self.fields} | self.defaults

    @field_validator("path")
    @classmethod
    def suggested_path(cls, value: str | None) -> str | None:
        return content_path(value) if value is not None else None

    @model_validator(mode="after")
    def distinct_options(self) -> Self:
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError("duplicate option keys")
        bounded_options(self.initial_options())
        return self


class PageManifest(PublicModel):
    protocol: Literal["pages-1"] = "pages-1"
    pages: list[PageRegistration] = Field(max_length=100)

    @model_validator(mode="after")
    def distinct_page_types(self) -> Self:
        ids = {page.id for page in self.pages}
        if (
            len(ids) != len(self.pages)
            or CUSTOM_PAGE_TYPE in ids
            or any(identity.startswith(TEMPLATE_TYPE_PREFIX) for identity in ids)
        ):
            raise ValueError("duplicate or reserved page type ID")
        return self


class PageCode(PublicModel):
    edit_mode: PageEditMode = Field(default="source", exclude_if=lambda v: v == "source")
    component_document: ComponentDocument | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    html: str = Field(default="", max_length=200000)
    css: str = Field(default="", max_length=100000)
    javascript: str = Field(default="", max_length=100000)
    javascript_enabled: bool = False
    javascript_position: Literal["head", "body_end"] = "body_end"


class TitledPageCode(PageCode):
    layout: PageLayout = Field(default="wide", exclude_if=lambda v: v == "wide")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class PageSettings(TitledPageCode, SeoFields):
    component_parameters: dict[str, JsonValue] = Field(
        default_factory=dict, exclude_if=lambda v: not v
    )
    _component_parameters = field_validator("component_parameters")(bounded_options)
    captcha_read: CaptchaOverride = "inherit"
    captcha_comment: CaptchaOverride = "inherit"
    comments_open: bool = True
    show_image_viewer: bool = True
    show_glossary_tooltips: bool = True
    show_glossary_footnotes: bool = True
    enabled: bool = False
    options: dict[str, JsonValue] = Field(default_factory=dict)
    _options = field_validator("options")(bounded_options)


class PageAtPath(PageSettings):
    path: str = Field(min_length=2, max_length=500)
    _path = field_validator("path")(content_path)


class PageValue(PageAtPath):
    share_image_media_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


class PageInput(PageSettings):
    share_image_media_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    path: str | None = Field(
        default=None, description="Omit on first save to generate a URL from the title."
    )


class PageTemplateReference(PublicModel):
    id: UUID
    title: str
    revision: int = Field(ge=1)


class PageTemplateItem(PublicModel):
    media_urls: dict[str, MediaURL] = Field(default_factory=dict, exclude_if=lambda v: not v)
    id: UUID
    type_id: str
    revision: int = Field(ge=1)
    value: TitledPageCode


class SavePageTemplate(PublicModel):
    id: UUID
    expected_revision: int = Field(ge=0)
    operation_id: UUID
    value: TitledPageCode


class PublicManagedPage(PageAtPath):
    media_urls: dict[str, MediaURL] = Field(default_factory=dict, exclude_if=lambda v: not v)
    read_receipt: str = ""
    access: Literal["public", "captcha"] = "public"
    share_image_url: str | None = Field(default=None, exclude_if=lambda value: value is None)
    enabled: Literal[True] = True
    id: UUID
    source: Literal["custom", "registered"]
    implementation_id: str | None = None
    implementation_version: str | None = None
    revision: int = Field(ge=1)
    language: str | None = None
    translation_group_id: UUID | None = None
    is_original: bool = True


class PageTranslationMember(PublicModel):
    id: UUID
    language: str
    title: str
    path: str
    revision: int
    applied_revision: int | None
    enabled: bool
    archived: bool
    is_original: bool
    needs_review: bool


class PageTranslationCatalog(PublicModel):
    id: UUID
    source_id: UUID
    revision: int
    members: list[PageTranslationMember]


class ManagedPageItem(PublicModel):
    media_urls: dict[str, MediaURL] = Field(default_factory=dict, exclude_if=lambda v: not v)
    id: UUID
    source: Literal["custom", "registered"]
    implementation_id: str | None
    type_id: str
    template: PageTemplateReference | None = None
    revision: int
    applied_revision: int | None
    enabled: bool
    value: PageValue
    share_image_url: str | None = None
    registration: PageRegistration | None
    available: bool
    updated_at: datetime
    archived: bool
    has_applied_snapshot: bool = False
    has_unapplied_changes: bool = False
    enable_requires_password: bool = Field(default=False, description="Legacy field; always false.")
    applied_javascript_enabled: bool = False
    language: str | None = None
    translation_group_id: UUID | None = None
    is_original: bool = True
    translations: PageTranslationCatalog | None = None

    @model_validator(mode="before")
    @classmethod
    def old_operation_receipt(cls, value: object) -> object:
        if isinstance(value, dict) and "type_id" not in value:
            return {**value, "type_id": value.get("implementation_id") or CUSTOM_PAGE_TYPE}
        return value


class ManagedPageSummary(PublicModel):
    id: UUID
    source: Literal["custom", "registered"]
    implementation_id: str | None
    type_id: str
    type_title: str
    title: str
    path: str
    description: str
    revision: int
    applied_revision: int | None
    enabled: bool
    available: bool
    archived: bool
    updated_at: datetime
    has_applied_snapshot: bool = False
    has_unapplied_changes: bool = False
    enable_requires_password: bool = Field(default=False, description="Legacy field; always false.")
    applied_javascript_enabled: bool = False
    language: str | None = None
    translation_group_id: UUID | None = None
    is_original: bool = True
    translations: PageTranslationCatalog | None = None


class ManagedPageCatalog(PublicModel):
    items: list[ManagedPageSummary]


class PageTypeItem(PublicModel):
    id: str
    source: Literal["custom", "registered", "template"]
    title: str
    description: str = ""
    registration: PageRegistration | None = None
    template_id: UUID | None = None
    available: bool
    page_count: int = Field(ge=0)


class PageTypeCatalog(PublicModel):
    items: list[PageTypeItem]


class PageCategory(PublicModel):
    id: UUID
    name: str
    slug: str


class PageCategories(PublicModel):
    items: list[PageCategory]


class SaveManagedPage(PublicModel):
    id: UUID
    expected_revision: int = Field(ge=0)
    value: PageInput
    apply: bool = False
    operation_id: UUID
    current_password: str = Field(default="", max_length=1024, description="Legacy field; ignored.")
    language: LanguageCode | None = None
    type_id: str | None = Field(default=None, pattern=PAGE_TYPE_PATTERN, max_length=100)


class SetPageEnabled(PublicModel):
    id: UUID
    expected_revision: int = Field(ge=1)
    enabled: bool
    apply_draft: bool = False
    operation_id: UUID
    current_password: str = Field(default="", max_length=1024, description="Legacy field; ignored.")


class PageTranslationLookup(PublicModel):
    id: UUID


class PageTranslationMutation(PageTranslationLookup):
    expected_revision: int = Field(ge=1)
    operation_id: UUID


class CreatePageTranslation(PageTranslationMutation):
    language: LanguageCode
    copy_source: bool = False


class LinkPageTranslation(PageTranslationMutation):
    target_id: UUID


class RemoveManagedPage(PublicModel):
    restore: bool = False
    id: UUID
    expected_revision: int = Field(ge=1)
    operation_id: UUID


class PageMetadata(PublicModel):
    page_id: UUID
    revision: int = Field(ge=0)
    value: dict[str, JsonValue] = Field(default_factory=dict)


class SavePageMetadata(PublicModel):
    expected_revision: int = Field(ge=0)
    operation_id: UUID
    value: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_metadata(self) -> Self:
        encoded = json.dumps(self.value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("private metadata exceeds 64 KiB")
        return self


class PagesContract(PublicModel):
    catalog: PublicResponse[ManagedPageCatalog]
    types: PublicResponse[PageTypeCatalog]
    categories: PublicResponse[PageCategories]
    item: PublicResponse[ManagedPageItem]
    template: PublicResponse[PageTemplateItem]
    save_template: SavePageTemplate
    save: SaveManagedPage
    remove: RemoveManagedPage
    manifest: PageManifest
    public_page: PublicManagedPage
    metadata: PublicResponse[PageMetadata]
    save_metadata: SavePageMetadata
    set_enabled: SetPageEnabled
    translations: PublicResponse[PageTranslationCatalog]
    translation_lookup: PageTranslationLookup
    translation_mutation: PageTranslationMutation
    create_translation: CreatePageTranslation
    link_translation: LinkPageTranslation
