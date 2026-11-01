"""Versioned settings; only these whitelisted values may leave the backend."""

from datetime import datetime
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.media.schemas import (
    IconAsset,
    IconCatalog,
    IconQuery,
    IconUpload,
    MediaCatalog,
    MediaDetails,
    MediaEdit,
    MediaItem,
    MediaLookup,
    MediaQuery,
    MediaUpdate,
    MediaUpload,
)


class SettingsModel(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def safe_url(value: str) -> str:
    if not value:
        return value
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise ValueError("invalid URL")
    parsed = urlsplit(value)
    if value.startswith("/") and not value.startswith("//"):
        return value
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("requires a site path or HTTP(S) URL")
    return value


class MenuLink(SettingsModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=60)
    href: str = Field(min_length=1, max_length=2048)
    icon: str = Field(
        default="link",
        pattern=r"^(?:[a-z][a-z-]{0,31}|media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$",
    )
    _url = field_validator("href")(safe_url)


class MenuItem(MenuLink):
    children: list["MenuItem"] = Field(default_factory=list)


MediaReference = r"^(?:|media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$"


class SitePreferences(SettingsModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=300)
    author: str = Field(min_length=1, max_length=100)
    avatar_media_id: str = Field(default="", pattern=MediaReference)
    avatar_url: str = Field(default="", max_length=2048)
    notice_title: str = Field(default="", max_length=120)
    notice: str = Field(default="", max_length=1000)
    navigation: list[MenuItem] = Field(default_factory=list)
    seo_description: str = Field(default="", max_length=300)
    title_template: str = Field(default="%s · %site%", max_length=160)
    allow_indexing: bool = True
    _avatar = field_validator("avatar_url")(safe_url)

    @field_validator("name", "author")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("title_template")
    @classmethod
    def title_placeholders(cls, value: str) -> str:
        if value.replace("%site%", "").count("%s") != 1:
            raise ValueError("requires one %s placeholder")
        return value

    @model_validator(mode="after")
    def unique_menu_ids(self) -> Self:
        ids = []
        pending = list(self.navigation)
        while pending:
            item = pending.pop()
            ids.append(item.id)
            if len(ids) > 256:
                raise ValueError("at most 256 menu entries")
            pending.extend(item.children)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate menu identifiers")
        return self


class ThemePreferences(SettingsModel):
    accent: str = Field(default="#673bb7", pattern=r"^#[0-9a-fA-F]{6}$")
    mode: Literal["system", "light", "dark"] = "system"
    hero_title: str = Field(default="", max_length=160)
    hero_subtitle: str = Field(default="", max_length=300)
    background_media_id: str = Field(default="", pattern=MediaReference)
    background_video_media_id: str = Field(default="", pattern=MediaReference)
    background_url: str = Field(default="", max_length=2048)
    background_video_url: str = Field(
        default="/reference/campfire-background.webm", max_length=2048
    )
    background_play: bool = True
    _urls = field_validator("background_url", "background_video_url")(safe_url)


class PublicSettings(SettingsModel):
    site: SitePreferences
    theme: ThemePreferences
    icon_urls: dict[str, str] = Field(default_factory=dict)


class CachePolicy(SettingsModel):
    """First operational policy: request lifetime, refresh freshness and batching."""

    dynamic_freshness_seconds: int = Field(default=30, ge=5, le=3600)
    request_timeout_seconds: int = Field(default=8, ge=1, le=30)
    retry_delay_ms: int = Field(default=1500, ge=100, le=5000)
    batch_delay_ms: int = Field(default=24, ge=0, le=250)


class SaveSettings(SettingsModel):
    expected_revision: int = Field(ge=1)
    value: PublicSettings


class SavePolicy(SettingsModel):
    expected_revision: int = Field(ge=1)
    value: CachePolicy


class DeliveryState(SettingsModel):
    node_id: str
    enabled: bool
    desired_revision: int
    applied_revision: int | None
    status: Literal["not_configured", "pending", "applied", "failed"]
    applied_at: datetime | None
    error: str | None


class SettingsState(SettingsModel):
    source_epoch: str
    revision: int
    value: PublicSettings
    delivery: list[DeliveryState]


class PolicyState(SettingsModel):
    node_id: str
    source_epoch: str
    revision: int
    value: CachePolicy
    delivery: DeliveryState


class NodeList(SettingsModel):
    nodes: list[PolicyState]


class SettingsDelivery(SettingsModel):
    protocol: Literal["settings-1"] = "settings-1"
    node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    source_epoch: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    resource: Literal["public-settings", "cache-policy"]
    revision: int = Field(ge=1)
    value: PublicSettings | CachePolicy

    @model_validator(mode="after")
    def matching_value(self) -> Self:
        expected = PublicSettings if self.resource == "public-settings" else CachePolicy
        if not isinstance(self.value, expected):
            raise ValueError("resource/value mismatch")
        return self


class DeliveryReceipt(SettingsModel):
    node_id: str
    source_epoch: str
    resource: Literal["public-settings", "cache-policy"]
    revision: int
    digest: str
    durable: Literal[True]


class SettingsContract(PublicModel):
    media_lookup: MediaLookup
    media_update: MediaUpdate
    media_edit: MediaEdit
    media_details: PublicResponse[MediaDetails]
    media_query: MediaQuery
    media_upload: MediaUpload
    media_catalog: PublicResponse[MediaCatalog]
    media_item: PublicResponse[MediaItem]
    icons: PublicResponse[IconCatalog]
    icon_query: IconQuery
    icon_upload: IconUpload
    icon: PublicResponse[IconAsset]
    settings: PublicResponse[SettingsState]
    save_settings: SaveSettings
    policy: PublicResponse[PolicyState]
    save_policy: SavePolicy
    nodes: PublicResponse[NodeList]
    delivery: SettingsDelivery
    receipt: DeliveryReceipt
