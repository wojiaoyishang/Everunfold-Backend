"""Versioned request and response models shared by the plugin contract exporter."""

import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.content.schemas import MediaURL
from app.modules.settings.languages import LanguageCode

from .matching import validate_regex

GlossaryStatus = Literal["draft", "published", "archived"]
GlossaryLanguage = Literal["*"] | LanguageCode
GlossaryHookScope = Literal["all", "content"]
GlossaryHookTargetKind = Literal["article", "shuoshuo", "page"]
GlossaryHookStatus = Literal["active", "archived"]
GlossaryHookMode = Literal["selector", "script"]


class GlossaryBlock(PublicModel):
    id: UUID
    type: Literal["markdown", "html"] = "markdown"
    content: str = Field(max_length=100_000)


class GlossaryMatchRule(PublicModel):
    kind: Literal["literal", "regex"] = "literal"
    pattern: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    case_sensitive: bool = False
    whole_word: bool = True
    priority: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_pattern(self) -> Self:
        if not self.pattern.strip():
            raise ValueError("匹配文字不能为空")
        if self.kind == "regex":
            validate_regex(self.pattern)
        return self


class GlossaryMatchedText(PublicModel):
    text: str
    case_sensitive: bool
    whole_word: bool
    priority: int


class GlossaryMatchPreviewInput(PublicModel):
    title: str = Field(default="", max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    auto_match_title: bool = True
    match_rules: list[GlossaryMatchRule] | None = Field(default=None, max_length=30)
    sample: str = Field(max_length=2000)


class GlossaryMatchPreviewOutput(PublicModel):
    hits: list[GlossaryMatchedText]


class GlossaryFields(PublicModel):
    language: GlossaryLanguage = "*"
    slug: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=500)
    body: list[GlossaryBlock] = Field(min_length=1, max_length=128)
    feature_media_id: UUID | None = None
    show_image_viewer: bool = True
    aliases: list[str] = Field(default_factory=list, max_length=20)
    auto_match_title: bool = True
    match_rules: list[GlossaryMatchRule] | None = Field(default=None, max_length=30)
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

    @field_validator("title")
    @classmethod
    def trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("summary")
    @classmethod
    def optional_summary(cls, value: str) -> str:
        return value.strip()

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
    def bounded_body(self) -> Self:
        if len({block.id for block in self.body}) != len(self.body):
            raise ValueError("正文块 ID 不可重复")
        if sum(len(block.content) for block in self.body) > 200_000:
            raise ValueError("正文不能超过 200000 字符")
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
    feature_media_id: UUID | None = None
    feature_image: MediaURL | None = None
    show_image_viewer: bool = True
    aliases: list[str]
    auto_match_title: bool
    match_rules: list[GlossaryMatchRule] | None
    status: GlossaryStatus
    revision: int
    updated_at: datetime
    published_at: datetime | None


class GlossaryDetail(GlossaryListItem):
    body: list[GlossaryBlock]
    feature_image: MediaURL | None = None
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)
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
    language: GlossaryLanguage | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=100)


class PublicGlossaryTerm(PublicModel):
    id: UUID
    language: str
    slug: str
    title: str
    summary: str
    feature_image: MediaURL | None = None
    show_image_viewer: bool = True
    aliases: list[str]
    path: str


class PublicGlossaryIndex(PublicModel):
    revision: str
    terms: list[PublicGlossaryTerm]


class PublicGlossaryDetail(PublicGlossaryTerm):
    body: list[GlossaryBlock]
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)


class PublicGlossaryMatch(PublicGlossaryTerm):
    body: list[GlossaryBlock]
    media_urls: dict[str, MediaURL] = Field(default_factory=dict)
    matched_texts: list[str]
    matched_patterns: list[GlossaryMatchedText] = Field(default_factory=list)


class ContentGlossaryMatches(PublicModel):
    content_id: UUID
    revision: str
    terms: list[PublicGlossaryMatch]


class GlossaryHookTarget(PublicModel):
    kind: GlossaryHookTargetKind
    id: UUID = Field(strict=False)


def validate_css_selector(value: str) -> str:
    selector = value.strip()
    if not 2 <= len(selector) <= 256:
        raise ValueError("选择器必须为 2 至 256 个字符")
    if any(char in selector for char in "\x00\r\n{}<>"):
        raise ValueError("选择器包含不安全字符")
    if "::" in selector:
        raise ValueError("不支持伪元素选择器")
    if re.search(
        r"(?:^|[\s,>+~])(?:html|body|\*)(?=$|[\s,.#:[>+~])",
        selector,
        flags=re.IGNORECASE,
    ):
        raise ValueError("不允许使用过于宽泛的选择器")
    return selector


class GlossaryHookFields(PublicModel):
    mode: GlossaryHookMode = "selector"
    selector: str = Field(default="", max_length=256)
    script: str = Field(default="", max_length=20000)
    scope: GlossaryHookScope = "all"
    targets: list[GlossaryHookTarget] = Field(default_factory=list, max_length=32)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=100)

    @field_validator("selector")
    @classmethod
    def valid_selector(cls, value: str) -> str:
        return validate_css_selector(value) if value.strip() else ""

    @field_validator("script")
    @classmethod
    def safe_script(cls, value: str) -> str:
        if "\x00" in value or "\r" in value:
            raise ValueError("脚本包含不支持的字符")
        return value

    @model_validator(mode="after")
    def valid_targets(self) -> Self:
        keys = [(target.kind, target.id) for target in self.targets]
        if len(keys) != len(set(keys)):
            raise ValueError("触发目标不可重复")
        if self.scope == "all" and self.targets:
            raise ValueError("所有页面范围不能同时指定内容目标")
        if self.scope == "content" and not self.targets:
            raise ValueError("指定内容范围至少需要一个目标")
        if self.mode == "selector" and not self.selector:
            raise ValueError("CSS 选择器不能为空")
        if self.mode == "script" and not self.script.strip():
            raise ValueError("JavaScript 挂载脚本不能为空")
        return self


class GlossaryHookCreate(PublicModel):
    operation_id: UUID
    hook_id: UUID
    term_id: UUID
    fields: GlossaryHookFields


class GlossaryHookUpdate(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    term_id: UUID
    fields: GlossaryHookFields


class GlossaryHookArchive(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)


class GlossaryHookQuery(PublicModel):
    search: str = Field(default="", max_length=200)
    status: Literal["all", "active", "archived"] = "all"
    term_id: UUID | None = Field(default=None, strict=False)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=100)


class GlossaryHookItem(PublicModel):
    id: UUID
    term: PublicGlossaryTerm
    mode: GlossaryHookMode
    selector: str
    script: str
    scope: GlossaryHookScope
    targets: list[GlossaryHookTarget]
    enabled: bool
    priority: int
    status: GlossaryHookStatus
    revision: int
    updated_at: datetime


class GlossaryHookList(PublicModel):
    items: list[GlossaryHookItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)


class PublicGlossaryHook(PublicModel):
    id: UUID
    mode: GlossaryHookMode
    selector: str
    script: str
    scope: GlossaryHookScope
    priority: int
    term: PublicGlossaryDetail


class PublicGlossaryHooks(PublicModel):
    revision: str
    hooks: list[PublicGlossaryHook]


class GlossaryContract(PublicModel):
    public_index: PublicResponse[PublicGlossaryIndex]
    public_detail: PublicResponse[PublicGlossaryDetail]
    content_matches: PublicResponse[ContentGlossaryMatches]
    preview_input: GlossaryMatchPreviewInput
    preview_output: PublicResponse[GlossaryMatchPreviewOutput]
    query: GlossaryQuery
    create: GlossaryCreate
    update: GlossaryUpdate
    list: PublicResponse[GlossaryList]
    detail: PublicResponse[GlossaryDetail]
    public_hooks: PublicResponse[PublicGlossaryHooks]
    hook_query: GlossaryHookQuery
    hooks_query: PublicResponse[GlossaryHookList]
    hook_detail: PublicResponse[GlossaryHookItem]
    hook_create: GlossaryHookCreate
    hook_update: GlossaryHookUpdate
    hook_archive: GlossaryHookArchive
