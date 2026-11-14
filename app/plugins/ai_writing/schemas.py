"""Private configuration, typed authoring targets and generation responses."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.plugin_sdk import PublicModel

Scene = Literal[
    "content.article.edit", "content.shuoshuo.edit", "pages.edit", "glossary.edit", "settings.site"
]
Status = Literal["queued", "running", "succeeded", "failed", "unknown"]
SceneFields = dict[Scene, list[str]]

PLUGIN_ID = "core.ai-writing"
TASK_ID = "core.ai-writing.generate"
MAX_PROVIDERS = 20
SEO_FIELDS = {
    "seo_title": (200, "简洁准确的搜索标题"),
    "seo_description": (500, "自然、准确的搜索结果描述"),
    "seo_share_title": (200, "适合分享卡片的标题"),
    "seo_share_description": (500, "适合分享卡片的简短描述"),
}
TARGETS: dict[Scene, dict[str, tuple[int, str]]] = {
    "content.article.edit": {"excerpt": (500, "概括主要内容的文章摘要"), **SEO_FIELDS},
    "content.shuoshuo.edit": SEO_FIELDS,
    "pages.edit": {"description": (500, "概括页面用途和内容的说明"), **SEO_FIELDS},
    "glossary.edit": {"summary": (500, "简短、易懂的术语解释"), **SEO_FIELDS},
    "settings.site": {
        "description": (500, "面向访客的站点简介"),
        "seo_description": (300, "概括站点主题的搜索结果描述"),
    },
}
PERMISSIONS: dict[Scene, str] = {
    "content.article.edit": "content:write",
    "content.shuoshuo.edit": "content:write",
    "pages.edit": "pages:write",
    "glossary.edit": "content:write",
    "settings.site": "settings:write",
}


class AiProviderSummary(PublicModel):
    id: UUID
    name: str
    model: str


class AiAvailability(PublicModel):
    revision: int
    enabled: bool
    fields: SceneFields
    default_provider_id: UUID | None
    providers: list[AiProviderSummary]
    restricted_by_default: dict[str, bool]


class AiProviderConfiguration(AiProviderSummary):
    base_url: str
    api_key_configured: bool


class AiConfiguration(PublicModel):
    revision: int = Field(ge=0)
    enabled: bool
    fields: SceneFields
    default_provider_id: UUID | None
    providers: list[AiProviderConfiguration]


class SaveAiProvider(PublicModel):
    id: UUID
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=160)
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False

    @field_validator("name", "base_url", "model", mode="before")
    @classmethod
    def trim(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_credential_change(self) -> Self:
        if self.api_key is not None and (
            self.clear_api_key
            or not self.api_key.get_secret_value()
            or any(ord(char) < 33 or ord(char) > 126 for char in self.api_key.get_secret_value())
        ):
            raise ValueError("Invalid credential change")
        return self


class SaveAiConfiguration(PublicModel):
    revision: int = Field(ge=0)
    enabled: bool
    fields: SceneFields
    default_provider_id: UUID | None
    providers: list[SaveAiProvider] = Field(max_length=MAX_PROVIDERS)

    @field_validator("fields")
    @classmethod
    def recognized_fields(cls, fields: SceneFields) -> SceneFields:
        for scene, names in fields.items():
            if len(names) != len(set(names)) or not set(names) <= TARGETS[scene].keys():
                raise ValueError("Unknown or duplicate writing field")
        return {scene: sorted(names) for scene, names in fields.items() if names}

    @model_validator(mode="after")
    def valid_configuration(self) -> Self:
        ids = {provider.id for provider in self.providers}
        names = {provider.name.casefold() for provider in self.providers}
        if len(ids) != len(self.providers) or len(names) != len(self.providers):
            raise ValueError("Duplicate provider")
        if ids and self.default_provider_id not in ids:
            raise ValueError("Default provider must be configured")
        if not ids and (self.enabled or self.default_provider_id is not None):
            raise ValueError("Enabled configuration requires a default provider")
        return self


class AiProviderRequest(PublicModel):
    request_id: UUID
    provider_id: UUID | None = None


class TestAiConnection(AiProviderRequest):
    expected_revision: int | None = Field(default=None, ge=0)


class GenerateAiText(AiProviderRequest):
    scene: Scene
    field: str = Field(min_length=1, max_length=40)
    resource_id: UUID | None = None
    language: str = Field(
        min_length=2, max_length=32, pattern=r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$"
    )
    title: str = Field(max_length=300)
    summary: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=8000)
    restricted: bool = False
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def target_exists(self) -> Self:
        if self.field not in TARGETS[self.scene]:
            raise ValueError("Unknown writing field")
        return self


class AiGeneration(PublicModel):
    id: UUID
    status: Status
    text: str | None
    error: str | None
