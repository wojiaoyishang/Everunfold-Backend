"""Provider-neutral CAPTCHA protocol; secrets have a write-only transport."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.schemas import PublicModel, PublicResponse

CaptchaAction = Literal["login", "read", "comment"]
CaptchaOverride = Literal["inherit", "required", "off"]


class ContentRules(PublicModel):
    article: bool = False
    shuoshuo: bool = False
    page: bool = False


class CaptchaRules(PublicModel):
    login: bool = False
    read: ContentRules = Field(default_factory=ContentRules)
    comment: ContentRules = Field(default_factory=ContentRules)


class ProviderSettings(PublicModel):
    site_key: str = Field(default="", max_length=300)
    hostnames: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("hostnames")
    @classmethod
    def exact_hosts(cls, values: list[str]) -> list[str]:
        import re

        result = sorted({value.strip().lower() for value in values if value.strip()})
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", v) for v in result):
            raise ValueError("请输入完整主机名，不包含协议、端口或通配符")
        return result


class StoredProvider(ProviderSettings):
    secret: str = Field(default="", max_length=2048, repr=False)


class CaptchaConfig(PublicModel):
    revision: int = Field(default=1, ge=1)
    provider: str = Field(default="local", max_length=100)
    rules: CaptchaRules = Field(default_factory=CaptchaRules)
    providers: dict[str, StoredProvider] = Field(default_factory=dict)


class ProviderDescription(PublicModel):
    id: str
    title: str
    widget: str


class PublicProviderSettings(ProviderSettings):
    secret_configured: bool


class CaptchaSettingsState(PublicModel):
    revision: int
    provider: str
    rules: CaptchaRules
    providers: dict[str, PublicProviderSettings]
    catalog: list[ProviderDescription]


class SaveProvider(ProviderSettings):
    secret: str | None = Field(default=None, max_length=2048, repr=False)
    clear_secret: bool = False


class SaveCaptchaSettings(PublicModel):
    remove_providers: list[str] = Field(default_factory=list, max_length=30)
    expected_revision: int = Field(ge=1)
    provider: str = Field(max_length=100)
    rules: CaptchaRules
    providers: dict[str, SaveProvider] = Field(max_length=30)


class CaptchaAnswer(PublicModel):
    challenge_id: UUID
    client_id: UUID
    answer: str = Field(min_length=1, max_length=4096, repr=False)


class ChallengeRequest(PublicModel):
    action: CaptchaAction
    content_id: UUID | None = None
    client_id: UUID

    @model_validator(mode="after")
    def target(self) -> Self:
        if (self.action == "login") != (self.content_id is None):
            raise ValueError("login has no content; other actions require content")
        return self


class CaptchaChallenge(PublicModel):
    required: bool
    provider: str = "local"
    widget: str = ""
    site_key: str = ""
    challenge_id: UUID | None = None
    expires_in: int = 180
    image: str = ""


class CaptchaContract(PublicModel):
    challenge_request: ChallengeRequest
    challenge_response: PublicResponse[CaptchaChallenge]
    settings_response: PublicResponse[CaptchaSettingsState]
    save_settings: SaveCaptchaSettings
