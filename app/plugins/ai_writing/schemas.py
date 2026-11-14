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


class AiAvailability(PublicModel):
    revision: int
    enabled: bool
    fields: SceneFields
    provider: str
    restricted_by_default: dict[str, bool]


class AiConfiguration(PublicModel):
    revision: int = Field(ge=0)
    enabled: bool
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=160)
    fields: SceneFields
    api_key_configured: bool


class SaveAiConfiguration(PublicModel):
    revision: int = Field(ge=0)
    enabled: bool
    base_url: str = Field(max_length=2048)
    model: str = Field(max_length=160)
    fields: SceneFields
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False

    @field_validator("base_url", "model")
    @classmethod
    def trim(cls, value: str) -> str:
        return value.strip()

    @field_validator("fields")
    @classmethod
    def recognized_fields(cls, fields: SceneFields) -> SceneFields:
        for scene, names in fields.items():
            if len(names) != len(set(names)) or not set(names) <= TARGETS[scene].keys():
                raise ValueError("Unknown or duplicate writing field")
        return {scene: sorted(names) for scene, names in fields.items() if names}

    @model_validator(mode="after")
    def valid_configuration(self) -> Self:
        if self.enabled and (not self.base_url or not self.model):
            raise ValueError("Enabled configuration requires an endpoint and model")
        if self.api_key and (
            self.clear_api_key
            or not self.api_key.get_secret_value().strip()
            or any(ord(char) < 33 or ord(char) > 126 for char in self.api_key.get_secret_value())
        ):
            raise ValueError("Invalid credential change")
        return self


class TestAiConnection(PublicModel):
    request_id: UUID


class GenerateAiText(TestAiConnection):
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
