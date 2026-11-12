"""Optimistic writes and reliable intents; one commit per use case, no network I/O."""

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import Content, SiteSettings, Taxonomy
from app.modules.media.locking import lock_references
from app.modules.media.service import MediaService
from app.modules.pages.models import ManagedPage
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.languages import language_route_pattern
from app.modules.settings.models import NodePolicy, SettingsAudit, SettingsDeliveryEvent
from app.modules.settings.schemas import (
    CachePolicy,
    DeliveryState,
    NodeList,
    PolicyState,
    PublicSettings,
    SavePolicy,
    SaveSettings,
    SettingsDelivery,
    SettingsState,
)
from app.modules.settings.translation_service import TranslationService

Resource = Literal["public-settings", "cache-policy", "public-content"]


class SettingsService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.config = settings

    async def site(self, *, locked: bool = False) -> SiteSettings:
        statement = select(SiteSettings).where(SiteSettings.id == 1)
        record = await self.session.scalar(statement.with_for_update() if locked else statement)
        if record is None:
            raise AuthFailure(503, 50320, "站点设置尚未初始化，请先执行数据库初始化")
        return record

    async def resolve(self, value: PublicSettings) -> PublicSettings:
        pending = list(value.site.navigation)
        icons: set[str] = set()
        while pending:
            item = pending.pop()
            if item.icon.startswith("media:"):
                icons.add(item.icon)
            pending.extend(item.children)
        service = MediaService(self.session, self.config)
        references: dict[str, str] = {}
        active_theme = value.extensions.theme or "argon"
        profiles = {
            key: profile.model_copy(deep=True) for key, profile in value.theme_options.items()
        }
        profiles.setdefault(active_theme, value.theme.model_copy(deep=True))
        for reference, kind in [
            (value.site.avatar_media_id, "image"),
            (value.site.icon_media_id, "image"),
            *[(profile.author_avatar_media_id, "image") for profile in profiles.values()],
            *[(profile.header_logo_media_id, "image") for profile in profiles.values()],
            *[(profile.font_media_id, "font") for profile in profiles.values()],
            *[(profile.code.font_media_id, "font") for profile in profiles.values()],
            *[(profile.background_media_id, "image") for profile in profiles.values()],
            *[(profile.background_video_media_id, "video") for profile in profiles.values()],
        ]:
            if reference:
                if reference in references and references[reference] != kind:
                    raise AuthFailure(422, 42234, "同一媒体不能同时作为图片和视频")
                references[reference] = kind
        urls = await service.resolve_resources(references)
        site = value.site.model_copy()
        for profile in profiles.values():
            for font in (profile, profile.code):
                font.font_url = ""
                font.font_info = None
                if not font.font_media_id:
                    continue
                from uuid import UUID

                from app.modules.media.models import MediaAsset
                from app.modules.media.schemas import FontInfo

                asset = await self.session.get(MediaAsset, UUID(font.font_media_id[6:]))
                if asset is None or asset.font_info is None:
                    raise AuthFailure(422, 42234, "请选择有效的字体")
                font.font_url = urls[font.font_media_id]
                font.font_info = FontInfo.model_validate(asset.font_info)
            if profile.background_media_id:
                profile.background_url = urls[profile.background_media_id]
            if profile.background_video_media_id:
                profile.background_video_url = urls[profile.background_video_media_id]
            if profile.author_avatar_media_id:
                profile.author_avatar_url = urls[profile.author_avatar_media_id]
            if profile.header_logo_media_id:
                profile.header_logo_url = urls[profile.header_logo_media_id]
        if site.icon_media_id:
            site.icon_url = urls[site.icon_media_id]
        if site.avatar_media_id:
            site.avatar_url = urls[site.avatar_media_id]
        theme = profiles[active_theme]
        resolved = value.model_copy(
            update={
                "site": site,
                "theme": theme,
                "theme_options": profiles,
                "icon_urls": await service.resolve_icons(icons),
                "translations": await TranslationService(self.session, self.config).overrides(),
            }
        )
        # Template media use the same immutable IDs as content; derived addresses
        # are refreshed with the settings projection, never written into sources.
        from app.modules.media.models import MediaAsset

        resolved.shortcodes = []
        for definition in value.shortcodes:
            ids = {
                uuid.UUID(item)
                for item in re.findall(
                    r"site://media/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
                    definition.html
                    + json.dumps([item.model_dump() for item in definition.parameters]),
                )
            }
            assets = (
                list(
                    (
                        await self.session.scalars(
                            select(MediaAsset).where(
                                MediaAsset.id.in_(ids),
                                MediaAsset.status == "ready",
                                MediaAsset.deleted_at.is_(None),
                            )
                        )
                    ).all()
                )
                if ids
                else []
            )
            if len(assets) != len(ids):
                raise AuthFailure(422, 42234, "短代码引用的媒体不可用")
            resolved.shortcodes.append(
                definition.model_copy(
                    update={
                        "media_urls": {str(asset.id): service.dto(asset).url for asset in assets}
                    }
                )
            )
        # The receiver accepts 64 KiB including its versioned delivery envelope.
        # Reserve space for that envelope; derived media URLs can exceed input size.
        encoded = json.dumps(
            resolved.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )
        if len(encoded.encode("utf-8")) > 60 * 1024:
            raise AuthFailure(422, 42232, "设置及图标数据过大，请减少菜单项或缩短链接")
        return resolved

    async def state(self, record: SiteSettings | None = None) -> SettingsState:
        record = record or await self.site()
        return SettingsState(
            source_epoch=str(record.source_epoch),
            revision=record.generation,
            value=await self.resolve(public_settings(record.data)),
            delivery=[
                await self.delivery(node, record.source_epoch, "public-settings", record.generation)
                for node in self.config.frontends
            ],
        )

    async def delivery(
        self, node: str, epoch: uuid.UUID, resource: Resource, revision: int
    ) -> DeliveryState:
        configured = self.config.frontends[node]
        enabled = configured.enabled and configured.settings_receiver_url is not None
        events = list(
            (
                await self.session.scalars(
                    select(SettingsDeliveryEvent)
                    .where(
                        SettingsDeliveryEvent.node_id == node,
                        SettingsDeliveryEvent.source_epoch == epoch,
                        SettingsDeliveryEvent.resource == resource,
                    )
                    .order_by(SettingsDeliveryEvent.revision.desc())
                )
            ).all()
        )
        applied = next((event for event in events if event.applied_at is not None), None)
        desired = next((event for event in events if event.revision == revision), None)
        status: Literal["not_configured", "pending", "applied", "failed"] = "not_configured"
        if enabled:
            status = "applied" if applied and applied.revision == revision else "pending"
            if status == "pending" and desired and desired.error:
                status = "failed"
        return DeliveryState(
            node_id=node,
            enabled=enabled,
            desired_revision=revision,
            applied_revision=applied.revision if applied else None,
            status=status,
            applied_at=applied.applied_at if applied else None,
            error=desired.error if desired else None,
        )

    async def enqueue(
        self, node: str, epoch: uuid.UUID, resource: Resource, revision: int, value: object
    ) -> None:
        payload = SettingsDelivery.model_validate(
            {
                "node_id": node,
                "source_epoch": str(epoch),
                "resource": resource,
                "revision": revision,
                "value": value,
            }
        ).model_dump(mode="json")
        await self.session.execute(
            insert(SettingsDeliveryEvent)
            .values(
                node_id=node,
                source_epoch=epoch,
                resource=resource,
                revision=revision,
                payload=payload,
            )
            .on_conflict_do_nothing(
                index_elements=["node_id", "source_epoch", "resource", "revision"]
            )
        )

    async def save(
        self, payload: SaveSettings, actor: uuid.UUID, *, shortcode_write: bool = False
    ) -> SettingsState:
        await lock_references(self.session)
        record = await self.site(locked=True)
        if record.generation != payload.expected_revision:
            raise AuthFailure(409, 40920, "设置已被修改，请重新读取后合并你的更改")
        before = await self.resolve(public_settings(record.data))
        payload.value = await self.resolve(payload.value)
        from app.core.plugins.state import public_shortcodes

        # Backend templates are immutable deployment data. The browser echoes the old
        # catalog while changing switches; the server derives the new active catalog.
        incoming = [
            item for item in payload.value.shortcodes if not item.name.startswith("custom:")
        ]
        previous = [item for item in before.shortcodes if not item.name.startswith("custom:")]
        if sorted(incoming, key=lambda item: item.id) != sorted(previous, key=lambda item: item.id):
            raise AuthFailure(422, 42280, "代码注册的短代码不能编辑")
        custom = [item for item in payload.value.shortcodes if item.name.startswith("custom:")]
        if not shortcode_write and custom != [
            item for item in before.shortcodes if item.name.startswith("custom:")
        ]:
            raise AuthFailure(422, 42280, "请在短代码库中应用模板修改")
        payload.value.shortcodes = [
            *public_shortcodes(record.data, payload.value.extensions.disabled_plugins),
            *custom,
        ]
        payload.value = await self.resolve(payload.value)
        if before.extensions != payload.value.extensions:
            from app.modules.settings.extensions_service import validate_activation

            await validate_activation(
                self.session, self.config, payload.value.extensions, before.extensions
            )
        for field in ("home_page_id", "posts_page_id"):
            page_id = getattr(payload.value.site.reading, field)
            if page_id and page_id != getattr(before.site.reading, field):
                page = await self.session.get(ManagedPage, page_id)
                if (
                    not page
                    or page.deleted_at
                    or page.source != "custom"
                    or not page.published
                    or (page.published.get("access", "public") != "public")
                ):
                    raise AuthFailure(422, 42236, "请选择已公开发布的自定义页面")
        reading = payload.value.site.reading
        if reading != before.site.reading and reading.home_page_id and reading.posts_page_id:
            home = await self.session.get(ManagedPage, reading.home_page_id)
            posts = await self.session.get(ManagedPage, reading.posts_page_id)
            if home and posts and home.translation_group_id == posts.translation_group_id:
                raise AuthFailure(422, 42236, "首页和文章列表页须使用不同的页面")
        category_id = payload.value.site.writing.default_category_id
        if category_id and category_id != before.site.writing.default_category_id:
            category = await self.session.get(Taxonomy, category_id)
            if not category or category.kind != "category" or category.retired:
                raise AuthFailure(422, 42237, "请选择仍可使用的文章分类")
        if before.site.language_catalog != payload.value.site.language_catalog:
            pattern = language_route_pattern(payload.value.site.language_catalog)
            if pattern and (
                await self.session.scalar(
                    select(Content.id)
                    .where(Content.path.op("~")(pattern), Content.publication_status == "published")
                    .limit(1)
                )
                or await self.session.scalar(
                    select(ManagedPage.id)
                    .where(
                        ManagedPage.deleted_at.is_(None),
                        or_(
                            ManagedPage.data["path"].astext.op("~")(pattern),
                            ManagedPage.published["path"].astext.op("~")(pattern),
                        ),
                    )
                    .limit(1)
                )
            ):
                raise AuthFailure(
                    422, 42294, "语言前缀与已有公开地址冲突，请更换前缀或先调整内容地址"
                )
        removed_languages = {item.code for item in before.site.language_catalog.languages} - {
            item.code for item in payload.value.site.language_catalog.languages
        }
        if removed_languages and (
            await self.session.scalar(
                select(ContentDraft.id).where(ContentDraft.language.in_(removed_languages)).limit(1)
            )
            or await self.session.scalar(
                select(Content.id).where(Content.language.in_(removed_languages)).limit(1)
            )
            or await self.session.scalar(
                select(ManagedPage.id).where(ManagedPage.language.in_(removed_languages)).limit(1)
            )
        ):
            raise AuthFailure(422, 42292, "该语言仍有内容，请保留语言或改为禁用")
        if before != payload.value:
            record.data = stored_settings(record.data, payload.value)
            record.generation += 1
            self.session.add(
                SettingsAudit(
                    user_id=actor,
                    resource="public-settings",
                    revision=record.generation,
                    changes={
                        "before": before.model_dump(mode="json"),
                        "after": payload.value.model_dump(mode="json"),
                    },
                )
            )
        for node, config in self.config.frontends.items():
            if config.enabled and config.settings_receiver_url:
                await self.enqueue(
                    node, record.source_epoch, "public-settings", record.generation, payload.value
                )
        await self.session.commit()
        return await self.state(record)

    def node(self, node: str) -> None:
        if node not in self.config.frontends:
            raise AuthFailure(404, 40420, "节点不存在")

    async def policy(self, node: str) -> PolicyState:
        self.node(node)
        record = await self.site()
        row = await self.session.get(NodePolicy, node)
        revision = row.revision if row else 1
        return PolicyState(
            node_id=node,
            source_epoch=str(record.source_epoch),
            revision=revision,
            value=CachePolicy.model_validate(row.data) if row else CachePolicy(),
            delivery=await self.delivery(node, record.source_epoch, "cache-policy", revision),
        )

    async def nodes(self) -> NodeList:
        return NodeList(nodes=[await self.policy(node) for node in self.config.frontends])

    async def save_policy(self, node: str, payload: SavePolicy, actor: uuid.UUID) -> PolicyState:
        self.node(node)
        site = await self.site(locked=True)
        row = await self.session.get(NodePolicy, node)
        revision = row.revision if row else 1
        before = CachePolicy.model_validate(row.data) if row else CachePolicy()
        if revision != payload.expected_revision:
            raise AuthFailure(409, 40920, "节点策略已被修改，请重新读取后合并你的更改")
        if row is None:
            row = NodePolicy(node_id=node, revision=1, data=before.model_dump())
            self.session.add(row)
        if before != payload.value:
            row.data = payload.value.model_dump()
            row.revision += 1
            self.session.add(
                SettingsAudit(
                    user_id=actor,
                    resource=f"cache-policy:{node}",
                    revision=row.revision,
                    changes={"before": before.model_dump(), "after": row.data},
                )
            )
        await self.enqueue(node, site.source_epoch, "cache-policy", row.revision, payload.value)
        await self.session.commit()
        return await self.policy(node)

    async def retry(self, node: str, actor: uuid.UUID) -> NodeList:
        self.node(node)
        site = await self.site(locked=True)
        policy = await self.policy(node)
        public = await self.resolve(public_settings(site.data))
        previous = await self.session.scalar(
            select(SettingsDeliveryEvent)
            .where(
                SettingsDeliveryEvent.source_epoch == site.source_epoch,
                SettingsDeliveryEvent.resource == "public-settings",
                SettingsDeliveryEvent.revision == site.generation,
            )
            .limit(1)
        )
        # Deployment-owned media URLs may change without editing menu identities.
        # Publish a new immutable revision instead of rewriting an acknowledged file.
        if previous and public != PublicSettings.model_validate(previous.payload["value"]):
            site.generation += 1
            self.session.add(
                SettingsAudit(
                    user_id=actor,
                    resource="public-settings",
                    revision=site.generation,
                    changes={"operation": "republish-derived-media-urls"},
                )
            )
            for target, config in self.config.frontends.items():
                if config.enabled and config.settings_receiver_url:
                    await self.enqueue(
                        target, site.source_epoch, "public-settings", site.generation, public
                    )
        for resource, revision, value in [
            ("public-settings", site.generation, public),
            ("cache-policy", policy.revision, policy.value),
        ]:
            await self.enqueue(node, site.source_epoch, resource, revision, value)  # type: ignore[arg-type]
        events = await self.session.scalars(
            select(SettingsDeliveryEvent)
            .where(
                SettingsDeliveryEvent.node_id == node,
                SettingsDeliveryEvent.source_epoch == site.source_epoch,
                or_(
                    and_(
                        SettingsDeliveryEvent.resource == "public-settings",
                        SettingsDeliveryEvent.revision == site.generation,
                    ),
                    and_(
                        SettingsDeliveryEvent.resource == "cache-policy",
                        SettingsDeliveryEvent.revision == policy.revision,
                    ),
                ),
            )
            .with_for_update()
        )
        for event in events:
            # Explicit resync also repairs a node whose previously acknowledged disk was lost.
            # Revoke the old lease so an in-flight stale sender cannot acknowledge this attempt.
            event.applied_at = None
            event.lease_id = None
            event.lease_until = None
            event.next_attempt_at = datetime.now(UTC)
            event.attempts = 0
            event.error = None
        await self.session.commit()
        return await self.nodes()
