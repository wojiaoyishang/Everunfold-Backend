"""Publish a complete public projection and its delivery intent in the caller's transaction."""

import json
from collections import deque
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.content.component_document import ComponentDocument
from app.modules.content.draft_models import ContentDraft
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media, publish_taxonomies
from app.modules.content.models import Content, SearchDocument, SiteSettings
from app.modules.content.publication_models import ContentRelease
from app.modules.content.publication_schemas import ContentDelivery, ContentSyncState
from app.modules.content.repository import ContentRepository, presentation_conditions
from app.modules.content.schemas import PublicSnapshot
from app.modules.content.service import PublicContentService
from app.modules.content.text_preview import content_preview
from app.modules.media.locking import lock_references
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.cache_events import stage_cache_events
from app.modules.settings.models import SettingsAudit, SettingsDeliveryEvent
from app.modules.settings.node_registry import read_directory
from app.modules.settings.service import SettingsService

_checked_cover_releases: deque[tuple[UUID, int]] = deque(maxlen=16)


def cache_path(item: Content | None) -> str | None:
    if (
        item
        and item.publication_status == "published"
        and item.visibility in {"public", "password"}
    ):
        return item.path
    return None


class ContentPublication:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session, self.config = session, config

    async def prepare(
        self, draft: ContentDraft, *, now: datetime | None = None
    ) -> tuple[EditorSettings, str]:
        if draft.type_id not in {"article", "shuoshuo"}:
            raise AuthFailure(422, 42254, "此类型尚未配置公开展示")
        if draft.edit_mode == "components":
            document = ComponentDocument.model_validate(draft.component_document).public_document()
            has_body = bool(document.nodes)
        else:
            has_body = any(str(block["content"]).strip() for block in draft.body)
        if not has_body:
            raise AuthFailure(422, 42255, "请填写正文后再发布")
        now = now or datetime.now(UTC)
        settings = EditorSettings.model_validate(draft.editor_settings or {})
        from app.modules.content.taxonomy_names import TermDirectory

        settings = (await TermDirectory.load(self.session)).normalize(settings)
        from app.modules.content.content_paths import initial_path

        path = (
            settings.path
            or draft.public_path
            or await initial_path(
                self.session,
                draft.title,
                draft.id,
                language=draft.language,
                type_id=draft.type_id,
                date=settings.display_published_at or now,
            )
        )
        from app.modules.pages.paths import page_path_taken

        if await page_path_taken(self.session, path) or await self.session.scalar(
            select(Content.id).where(Content.path == path, Content.id != draft.id)
        ):
            raise AuthFailure(409, 40954, "该地址已被其他内容使用，请修改路径")
        if settings.display_published_at and settings.display_published_at > now:
            raise AuthFailure(422, 42258, "显示时间不能晚于实际发布时间，请使用定时发布设置")
        from app.modules.settings.languages import language_catalog, reserved_language_path

        if reserved_language_path(path, await language_catalog(self.session)):
            raise AuthFailure(422, 42295, "该地址由语言导航使用，请更换内容地址")
        return settings, path

    async def publish(
        self, draft: ContentDraft, *, published_at: datetime | None = None
    ) -> CacheChange:
        now = datetime.now(UTC)
        settings, path = await self.prepare(draft, now=now)
        existing = await self.session.get(Content, draft.id)
        previous_path = cache_path(existing)
        draft.public_path = path
        draft.published_at = (
            settings.display_published_at or draft.published_at or published_at or now
        )
        draft.published_revision = draft.revision
        from app.modules.shortcodes.regions import (
            RegionPublication,
            require_no_regions,
            without_region_markers,
        )

        regions = RegionPublication(self.session, self.config, "content", draft.id)
        require_no_regions((draft.title, draft.author, settings.model_dump(mode="json")))
        component_document = None
        if draft.edit_mode == "components":
            document = ComponentDocument.model_validate(draft.component_document).public_document()
            document = regions.redact_document(document)
            assert document is not None
            component_document = document.model_dump(mode="json")
            public_body = []
            body = without_region_markers(document.text())
            source = document.model_dump_json()
            preview_blocks = [{"content": body}]
        else:
            public_body = [
                {**block, "content": regions.redact(str(block["content"]), block["type"])}
                for block in draft.body
            ]
            body = "\n\n".join(
                without_region_markers(str(block["content"])) for block in public_body
            )
            source = json.dumps(public_body)
            preview_blocks = [{"content": body}]
        media = await editor_media(self.session, self.config, source, settings)
        excerpt = regions.redact(draft.excerpt, "markdown") if draft.excerpt else ""
        excerpt = (
            content_preview([{"content": without_region_markers(excerpt)}], 1000)
            if excerpt != draft.excerpt
            else excerpt
        )
        excerpt = excerpt or content_preview(preview_blocks)
        await regions.store()
        from app.modules.captcha.service import current_config, required

        captcha_required = required(
            await current_config(self.session), "read", draft.type_id, settings.model_dump()
        )
        fields = dict(
            kind=draft.type_id,
            title=draft.title,
            excerpt=excerpt,
            path=draft.public_path,
            language=draft.language,
            translation_group_id=draft.translation_group_id,
            author=settings.byline or draft.author,
            published_at=draft.published_at,
            updated_at=now,
            reading_minutes=max(1, (len(body) + 399) // 400),
            word_count=len(body),
            content=public_body,
            component_document=component_document,
            media_urls={key: value.model_dump() for key, value in media.items()},
            cover_url=media[str(settings.cover_media_id)].original
            if settings.cover_media_id
            else None,
            pinned=settings.pinned,
            editor_settings=settings.model_dump(mode="json"),
            captcha_read_required=captcha_required,
            publication_status="published",
            visibility=settings.visibility,
            access_password_hash=draft.access_password_hash
            if settings.visibility == "password"
            else None,
            opens_at=settings.opens_at,
            open_synced_at=now if settings.opens_at and settings.opens_at <= now else None,
        )
        if existing is None:
            self.session.add(Content(id=draft.id, source="everunfold", source_id=None, **fields))
        else:
            for name, value in fields.items():
                setattr(existing, name, value)
            existing.public_generation += 1
        await self.session.flush()
        await publish_taxonomies(self.session, draft.id, settings)
        await self.session.execute(
            insert(SearchDocument)
            .values(content_id=draft.id, title=draft.title, body=body)
            .on_conflict_do_update(
                index_elements=["content_id"], set_={"title": draft.title, "body": body}
            )
        )
        paths = tuple(
            item
            for item in (
                previous_path,
                path if settings.visibility in {"public", "password"} else None,
            )
            if item is not None
        )
        return (
            CacheChange(
                "content_withdrawn"
                if previous_path and settings.visibility not in {"public", "password"}
                else "content_updated"
                if previous_path
                else "content_published",
                draft.id,
                paths,
            )
            if paths
            else CacheChange("projection_changed")
        )

    async def withdraw(self, draft: ContentDraft) -> CacheChange | None:
        if draft.published_revision is None:
            return None
        existing = await self.session.get(Content, draft.id)
        previous_path = cache_path(existing)
        if existing:
            existing.publication_status = "withdrawn"
            existing.public_generation += 1
        draft.published_revision = None
        return (
            CacheChange("content_withdrawn", draft.id, (previous_path,))
            if previous_path
            else CacheChange("projection_changed")
        )

    async def stage(self, *changes: CacheChange) -> ContentRelease:
        # All writers share the reference lock followed by this row lock. The snapshot and
        # immutable release are created before commit, without external network side effects.
        site = await SettingsService(self.session, self.config).site(locked=True)
        site.content_generation += 1
        await self.session.flush()
        snapshot = await PublicContentService(ContentRepository(self.session)).snapshot()
        if len(snapshot.model_dump_json().encode()) > 8 * 1024 * 1024 - 4096:
            raise AuthFailure(422, 42256, "公开内容超过当前同步容量，请调整存储方案后重试")
        release = ContentRelease(
            source_epoch=site.source_epoch,
            revision=site.content_generation,
            snapshot=snapshot.model_dump(mode="json"),
        )
        self.session.add(release)
        await self.enqueue(release)
        await stage_cache_events(
            self.session,
            site,
            "public-content",
            release.revision,
            changes or (CacheChange("projection_changed"),),
        )
        return release

    async def enqueue(self, release: ContentRelease, *, node_id: str | None = None) -> None:
        receivers = (await read_directory(self.session, self.config)).receivers
        for node in sorted(receivers if node_id is None else receivers & {node_id}):
            payload = ContentDelivery(
                node_id=node,
                source_epoch=str(release.source_epoch),
                revision=release.revision,
                value=PublicSnapshot.model_validate(release.snapshot),
            ).model_dump(mode="json")
            await self.session.execute(
                insert(SettingsDeliveryEvent)
                .values(
                    node_id=node,
                    source_epoch=release.source_epoch,
                    resource="public-content",
                    revision=release.revision,
                    payload=payload,
                )
                .on_conflict_do_nothing(
                    index_elements=["node_id", "source_epoch", "resource", "revision"]
                )
            )

    async def state(self) -> ContentSyncState:
        service = SettingsService(self.session, self.config)
        site = await service.site()
        directory = await read_directory(self.session, self.config)
        receivers = directory.receivers
        return ContentSyncState(
            source_epoch=str(site.source_epoch),
            revision=site.content_generation,
            delivery=[
                await service.delivery(
                    node,
                    site.source_epoch,
                    "public-content",
                    site.content_generation,
                    enabled=node in receivers,
                )
                for node in directory.entries
            ],
        )

    async def retry(self, actor_id: UUID) -> ContentSyncState:
        await self.stage_retry(actor_id)
        await self.session.commit()
        return await self.state()

    async def _omits_locked_article_covers(self, release: ContentRelease) -> bool:
        identities = [
            UUID(item["id"])
            for item in release.snapshot["contents"]
            if item.get("kind") == "article"
            and item.get("access") in {"password", "scheduled", "captcha"}
            and not item.get("cover_url")
        ]
        if not identities:
            return False
        return (
            await self.session.scalar(
                select(Content.id)
                .where(
                    *presentation_conditions(), Content.id.in_(identities), Content.cover_url != ""
                )
                .limit(1)
            )
            is not None
        )

    async def stage_legacy_cover_repair(self) -> bool:
        head = (
            await self.session.execute(
                select(SiteSettings.source_epoch, SiteSettings.content_generation).where(
                    SiteSettings.id == 1
                )
            )
        ).one_or_none()
        if head is None:
            return False
        epoch, revision = head
        current = (epoch, revision)
        if current in _checked_cover_releases:
            return False
        release = await self.session.get(ContentRelease, current)
        if release is None or not await self._omits_locked_article_covers(release):
            # The same release is immutable. Avoid reading the snapshot on each delivery tick.
            _checked_cover_releases.append(current)
            return False
        await lock_references(self.session)
        site = await SettingsService(self.session, self.config).site(locked=True)
        if current != (site.source_epoch, site.content_generation):
            return False
        await self.stage()
        return True

    async def stage_retry(self, actor_id: UUID | None, *, node_id: str | None = None) -> None:
        await lock_references(self.session)
        site = await SettingsService(self.session, self.config).site(locked=True)
        release = await self.session.get(
            ContentRelease, (site.source_epoch, site.content_generation)
        )
        if release is None or await self._omits_locked_article_covers(release):
            # A new projection advances the site-wide version and reaches all receivers.
            release = await self.stage()
        else:
            await self.enqueue(release, node_id=node_id)
        receivers = (await read_directory(self.session, self.config)).receivers
        events = await self.session.scalars(
            select(SettingsDeliveryEvent)
            .where(
                SettingsDeliveryEvent.source_epoch == release.source_epoch,
                SettingsDeliveryEvent.resource == "public-content",
                SettingsDeliveryEvent.revision == release.revision,
                SettingsDeliveryEvent.node_id.in_(
                    receivers if node_id is None else receivers & {node_id}
                ),
            )
            .with_for_update()
        )
        for event in events:
            event.applied_at = event.lease_id = event.lease_until = event.error = None
            event.attempts = 0
            event.next_attempt_at = datetime.now(UTC)
        if actor_id is not None:
            self.session.add(
                SettingsAudit(
                    user_id=actor_id,
                    resource="public-content",
                    revision=release.revision,
                    changes={"action": "retry", "source_epoch": str(release.source_epoch)},
                )
            )
