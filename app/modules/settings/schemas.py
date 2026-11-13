"""Versioned settings; only these whitelisted values may leave the backend."""

import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.core.jobs.progress_schemas import TaskProgress, WatchTask
from app.core.jobs.schemas import RuntimeCommand, RuntimeOverview
from app.core.schemas import PublicModel, PublicResponse
from app.modules.media.fonts import FontCatalog
from app.modules.media.schemas import (
    FontInfo,
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
    MediaRemoval,
    MediaRemovalIntent,
    MediaRemovalPreview,
    MediaRemove,
    MediaUpdate,
    MediaUpload,
)
from app.modules.settings.extensions_schemas import (
    ExtensionCatalog,
    ExtensionPreferences,
    ExtensionUsage,
    ExtensionUsageQuery,
)
from app.modules.settings.languages import LanguageCatalog
from app.modules.settings.mail_schemas import (
    MailSettingsState,
    MailTestLookup,
    MailTestState,
    SaveMailSettings,
    SendTestMail,
)
from app.modules.settings.nodes_schemas import (
    MonitorStatus,
    NodeChange,
    NodeChangeResult,
    NodeProof,
    NodeProofRequest,
    ReceiverStatus,
)
from app.modules.settings.publishing_preferences import ReadingPreferences, WritingPreferences
from app.modules.settings.translation_schemas import (
    SaveTranslations,
    TranslationCatalog,
    TranslationOverrides,
)
from app.modules.settings.translation_schemas import (
    TranslationState as TranslationSettingsState,
)
from app.modules.settings.urls import safe_url as safe_url
from app.modules.shortcodes.schemas import (
    PreviewShortcode,
    PublicShortcode,
    RestoreShortcode,
    SaveShortcode,
    ShortcodeAction,
    ShortcodeCatalog,
    ShortcodeCompilation,
    ShortcodeItem,
)


class SettingsModel(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NavigationReference(SettingsModel):
    kind: Literal["article", "shuoshuo", "page"]
    id: UUID = Field(strict=False)


class NavigationResource(NavigationReference):
    title: str
    path: str
    language: str | None


class NavigationQuery(SettingsModel):
    kind: Literal["article", "shuoshuo", "page"]
    id: UUID | None = Field(default=None, strict=False)
    search: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=100)


class NavigationCatalog(SettingsModel):
    items: list[NavigationResource]
    total: int
    page: int
    page_size: int


class MenuLink(SettingsModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=60)
    labels: dict[str, str] = Field(default_factory=dict, max_length=32)
    href: str = Field(min_length=1, max_length=2048)
    icon: str = Field(
        default="link",
        pattern=r"^(?:[a-z][a-z-]{0,31}|media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$",
    )
    _url = field_validator("href")(safe_url)
    reference: NavigationReference | None = None

    @field_validator("labels")
    @classmethod
    def localized_labels(cls, values: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for language, raw in values.items():
            if not re.fullmatch(r"[A-Za-z0-9*][A-Za-z0-9*-]{0,31}", language):
                raise ValueError("invalid navigation label language")
            label = " ".join(raw.split())
            if not label or len(label) > 60 or any(ord(char) < 32 for char in label):
                raise ValueError("navigation label must be 1 to 60 visible characters")
            result[language] = label
        return result


class MenuItem(MenuLink):
    children: list["MenuItem"] = Field(default_factory=list)


MediaReference = r"^(?:|media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$"


class SearchDiscovery(SettingsModel):
    sitemap_enabled: bool = True
    feeds_enabled: bool = True
    feed_content: Literal["excerpt", "full"] = "excerpt"
    feed_limit: int = Field(default=30, ge=1, le=100)
    structured_data: bool = True
    share_image: str = Field(default="", max_length=2048)
    google_verification: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_-]*$")
    bing_verification: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9_-]*$")
    _image = field_validator("share_image")(safe_url)


class GlossaryPreferences(SettingsModel):
    auto_match: bool = True
    max_matches_per_page: int = Field(default=20, ge=1, le=100)
    popup_source: Literal["summary", "content"] = "content"


class FooterLink(SettingsModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=60)
    href: str = Field(min_length=1, max_length=2048)
    icon: str = Field(
        default="link",
        pattern=r"^(?:[a-z][a-z-]{0,31}|media:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$",
    )
    _url = field_validator("href")(safe_url)


class FooterPreferences(SettingsModel):
    copyright: str = Field(default="", max_length=300)
    links: list[FooterLink] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len({item.id for item in self.links}) != len(self.links):
            raise ValueError("duplicate footer link identifiers")
        return self


class SitePreferences(SettingsModel):
    reading: ReadingPreferences = Field(default_factory=ReadingPreferences)
    writing: WritingPreferences = Field(default_factory=WritingPreferences)
    icon_media_id: str = Field(default="", pattern=MediaReference)
    icon_url: str = Field(default="", max_length=2048)
    _site_icon = field_validator("icon_url")(safe_url)
    discovery: SearchDiscovery = Field(default_factory=SearchDiscovery)
    footer: FooterPreferences = Field(default_factory=FooterPreferences)
    glossary: GlossaryPreferences = Field(default_factory=GlossaryPreferences)
    language_catalog: LanguageCatalog = Field(default_factory=LanguageCatalog)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=300)
    author: str = Field(min_length=1, max_length=100)
    author_tagline: str = Field(default="", max_length=160)
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


class CodePreferences(SettingsModel):
    light_scheme: Literal["paper", "github"] = "paper"
    dark_scheme: Literal["slate", "github"] = "slate"
    line_numbers: bool = True
    wrap: bool = False
    collapse_lines: int = Field(default=18, ge=0, le=200)
    font_size: int = Field(default=13, ge=11, le=20)
    line_height: float = Field(default=1.7, ge=1.3, le=2.2)
    tab_size: int = Field(default=4, ge=2, le=8)
    font_media_id: str = Field(default="", pattern=MediaReference)
    font_url: str = Field(default="", max_length=2048)
    font_info: FontInfo | None = None


ArticleMetaKey = Literal["author", "published", "updated", "words", "reading", "comments", "views"]
ARTICLE_META_ORDER: tuple[ArticleMetaKey, ...] = (
    "author",
    "published",
    "updated",
    "words",
    "reading",
    "comments",
    "views",
)


class ArticleDisplayPreferences(SettingsModel):
    show_updated: bool = False
    show_toc: bool = True
    meta_order: list[ArticleMetaKey] = Field(
        default_factory=lambda: list(ARTICLE_META_ORDER), max_length=7
    )
    cover_position: Literal["above", "below", "hidden"] = "above"
    words_per_minute: int = Field(default=200, ge=30, le=1500)
    han_per_minute: int = Field(default=400, ge=50, le=2000)
    stale_days: int = Field(default=0, ge=0, le=3650)
    stale_basis: Literal["updated", "published"] = "updated"
    related_basis: Literal["both", "category", "tag", "recent"] = "both"
    afterword: str = Field(default="", max_length=2000)

    @field_validator("meta_order")
    @classmethod
    def unique_meta(cls, values: list[ArticleMetaKey]) -> list[ArticleMetaKey]:
        return list(dict.fromkeys(values))

    show_author: bool = True
    show_date: bool = True
    show_word_count: bool = True
    show_reading_time: bool = True
    show_comments: bool = True
    show_views: bool = True
    show_share: bool = True
    show_neighbors: bool = True
    show_related: bool = True
    related_count: int = Field(default=6, ge=0, le=6)
    toc_numbered: bool = False


class FeedPreferences(SettingsModel):
    default_type: Literal["all", "article", "shuoshuo"] = "all"
    page_size: int = Field(default=10, ge=1, le=30)


class SidebarLink(SettingsModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    label: str = Field(min_length=1, max_length=60)
    href: str = Field(min_length=1, max_length=2048)

    @field_validator("label", "href", mode="before")
    @classmethod
    def trim(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    _url = field_validator("href")(safe_url)


class SidebarProfilePreferences(SettingsModel):
    display_name: str = Field(default="", max_length=100)
    show_tagline: bool = True
    show_stats: bool = True
    articles_url: str = Field(default="/archives", max_length=2048)
    categories_url: str = Field(default="/categories", max_length=2048)
    tags_url: str = Field(default="/tags", max_length=2048)
    friends_title: str = Field(default="Links", max_length=60)
    personal_links: list[SidebarLink] | None = Field(default=None, max_length=12)
    friend_links: list[SidebarLink] | None = Field(default=None, max_length=24)

    @field_validator("articles_url", "categories_url", "tags_url")
    @classmethod
    def stats_url(cls, value: str) -> str:
        return safe_url(value.strip())

    @field_validator("personal_links", "friend_links")
    @classmethod
    def unique_links(cls, links: list[SidebarLink] | None) -> list[SidebarLink] | None:
        if links is not None and len({link.id for link in links}) != len(links):
            raise ValueError("duplicate sidebar link identifiers")
        return links


class SidebarNavigationPreferences(SettingsModel):
    title: str | None = Field(default=None, max_length=160)
    alias: str | None = Field(default=None, max_length=100)
    tagline: str | None = Field(default=None, max_length=300)
    show_banner: bool = True
    show_search: bool = True
    search_label: str = Field(default="", max_length=60)
    menu: list[MenuItem] | None = None


class ThemePreferences(SettingsModel):
    author_avatar_media_id: str = Field(default="", pattern=MediaReference)
    author_avatar_url: str | None = Field(default=None, max_length=2048)
    header_brand_mode: Literal["text", "image", "both"] = "both"
    header_brand_text: str = Field(default="", max_length=160)
    header_logo_media_id: str = Field(default="", pattern=MediaReference)
    header_logo_url: str = Field(default="", max_length=2048)

    @field_validator("author_avatar_url", "header_logo_url")
    @classmethod
    def valid_brand_image(cls, value: str | None) -> str | None:
        return safe_url(value) if value is not None else None

    article: ArticleDisplayPreferences = Field(default_factory=ArticleDisplayPreferences)
    code: CodePreferences = Field(default_factory=CodePreferences)
    feed: FeedPreferences = Field(default_factory=FeedPreferences)
    font_media_id: str = Field(default="", pattern=MediaReference)
    font_url: str = Field(default="", max_length=2048)
    font_info: FontInfo | None = None
    accent: str = Field(default="#673bb7", pattern=r"^#[0-9a-fA-F]{6}$")
    mode: Literal["system", "light", "dark"] = "system"
    hero_title: str = Field(default="", max_length=160)
    hero_subtitle: str = Field(default="", max_length=300)
    hero_show_subtitle: bool = True
    hero_animation: Literal["none", "fade", "rise", "typewriter"] = "rise"
    hero_typing_ms: int = Field(default=30, ge=10, le=120)
    sidebar_profile: SidebarProfilePreferences = Field(default_factory=SidebarProfilePreferences)
    sidebar_navigation: SidebarNavigationPreferences = Field(
        default_factory=SidebarNavigationPreferences
    )
    background_media_id: str = Field(default="", pattern=MediaReference)
    background_video_media_id: str = Field(default="", pattern=MediaReference)
    background_url: str = Field(default="", max_length=2048)
    background_video_url: str = Field(
        default="/reference/campfire-background.webm", max_length=2048
    )
    background_play: bool = True
    paper_tone: Literal["ivory", "white", "gray"] = "ivory"
    navigation: list[MenuItem] | None = None
    _urls = field_validator("background_url", "background_video_url")(safe_url)

    @model_validator(mode="after")
    def unique_navigation_ids(self) -> Self:
        for menu in (self.navigation, self.sidebar_navigation.menu):
            ids: set[str] = set()
            pending = list(menu or [])
            while pending:
                item = pending.pop()
                if item.id in ids:
                    raise ValueError("duplicate menu identifiers")
                ids.add(item.id)
                if len(ids) > 256:
                    raise ValueError("at most 256 menu entries")
                pending.extend(item.children)
        return self


class PublicSettings(SettingsModel):
    extensions: ExtensionPreferences = Field(default_factory=ExtensionPreferences)
    site: SitePreferences
    theme: ThemePreferences
    theme_options: dict[str, ThemePreferences] = Field(default_factory=dict, max_length=20)
    icon_urls: dict[str, str] = Field(default_factory=dict)
    shortcodes: list[PublicShortcode] = Field(default_factory=list, max_length=100)
    translations: TranslationOverrides = Field(default_factory=TranslationOverrides)

    @model_validator(mode="after")
    def active_theme_projection(self) -> Self:
        import re

        if any(not re.fullmatch(r"[a-z][a-z0-9-]*", key) for key in self.theme_options):
            raise ValueError("invalid theme option identifier")
        active = self.extensions.theme or "argon"
        profiles = dict(self.theme_options)
        if active in profiles:
            self.theme = profiles[active]
        else:
            profiles[active] = self.theme
        self.theme_options = profiles
        return self


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
    watch_task: WatchTask
    task_progress: PublicResponse[TaskProgress]
    mail: PublicResponse[MailSettingsState]
    test_mail: SendTestMail
    test_mail_lookup: MailTestLookup
    test_mail_state: PublicResponse[MailTestState]
    save_mail: SaveMailSettings
    runtime: PublicResponse[RuntimeOverview]
    runtime_command: RuntimeCommand
    extensions: PublicResponse[ExtensionCatalog]
    extension_usage_query: ExtensionUsageQuery
    extension_usage: PublicResponse[ExtensionUsage]
    navigation_query: NavigationQuery
    navigation_catalog: PublicResponse[NavigationCatalog]
    shortcode_catalog: PublicResponse[ShortcodeCatalog]
    shortcode_item: PublicResponse[ShortcodeItem]
    shortcode_preview: PublicResponse[ShortcodeCompilation]
    save_shortcode: SaveShortcode
    shortcode_action: ShortcodeAction
    restore_shortcode: RestoreShortcode
    preview_shortcode: PreviewShortcode
    fonts: PublicResponse[FontCatalog]
    media_removal_intent: MediaRemovalIntent
    media_removal_preview: PublicResponse[MediaRemovalPreview]
    media_remove: MediaRemove
    media_removal: PublicResponse[MediaRemoval]
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
    translations: PublicResponse[TranslationSettingsState]
    translation_catalog: PublicResponse[TranslationCatalog]
    save_translations: SaveTranslations
    receiver_status: ReceiverStatus
    nodes_monitor_state: PublicResponse[MonitorStatus]
    nodes_monitor_command: NodeChange
    nodes_monitor_result: PublicResponse[NodeChangeResult]
    nodes_local_proof_request: NodeProofRequest
    nodes_local_proof: PublicResponse[NodeProof]
