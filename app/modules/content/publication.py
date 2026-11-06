"""Publish a complete public projection and its delivery intent in the caller's transaction."""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media, publish_taxonomies
from app.modules.content.models import Content, SearchDocument
from app.modules.content.publication_models import ContentRelease
from app.modules.content.publication_schemas import ContentDelivery, ContentSyncState
from app.modules.content.repository import ContentRepository
from app.modules.content.schemas import PublicSnapshot
from app.modules.content.service import PublicContentService
from app.modules.content.text_preview import content_preview
from app.modules.settings.models import SettingsAudit, SettingsDeliveryEvent
from app.modules.settings.service import SettingsService


class ContentPublication:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session, self.config = session, config

    async def prepare(
        self, draft: ContentDraft, *, now: datetime | None = None
    ) -> tuple[EditorSettings, str]:
        if draft.type_id not in {"article", "shuoshuo"}:
            raise AuthFailure(422, 42254, "此类型尚未配置公开展示")
        if not any(str(block["content"]).strip() for block in draft.body):
            raise AuthFailure(422, 42255, "请填写正文后再发布")
        now = now or datetime.now(UTC)
        settings = EditorSettings.model_validate(draft.editor_settings or {})
        from app.modules.content.content_paths import initial_path

        path = (
            settings.path
            or draft.public_path
            or await initial_path(self.session, draft.title, draft.id, language=draft.language)
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

    async def publish(self, draft: ContentDraft, *, published_at: datetime | None = None) -> None:
        now = datetime.now(UTC)
        settings, path = await self.prepare(draft, now=now)
        existing = await self.session.get(Content, draft.id)
        draft.public_path = path
        draft.published_at = (
            settings.display_published_at or draft.published_at or published_at or now
        )
        draft.published_revision = draft.revision
        body = "\n\n".join(str(block["content"]) for block in draft.body)
        media = await editor_media(self.session, self.config, json.dumps(draft.body), settings)
        excerpt = draft.excerpt or content_preview(draft.body)
        fields = dict(
            kind=draft.type_id,
            title=draft.title,
            excerpt=excerpt,
            path=draft.public_path,
            language=draft.language,
            translation_group_id=draft.translation_group_id,
            author=draft.author,
            published_at=draft.published_at,
            updated_at=now,
            reading_minutes=max(1, (len(body) + 399) // 400),
            word_count=len(body),
            content=draft.body,
            media_urls={key: value.model_dump() for key, value in media.items()},
            cover_url=media[str(settings.cover_media_id)].original
            if settings.cover_media_id
            else None,
            pinned=settings.pinned,
            editor_settings=settings.model_dump(mode="json"),
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

    async def withdraw(self, draft: ContentDraft) -> bool:
        if draft.published_revision is None:
            return False
        existing = await self.session.get(Content, draft.id)
        if existing:
            existing.publication_status = "withdrawn"
            existing.public_generation += 1
        draft.published_revision = None
        return True

    async def stage(self) -> ContentRelease:
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
        return release

    async def enqueue(self, release: ContentRelease) -> None:
        for node, config in self.config.frontends.items():
            if not config.enabled or not config.settings_receiver_url:
                continue
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
        return ContentSyncState(
            source_epoch=str(site.source_epoch),
            revision=site.content_generation,
            delivery=[
                await service.delivery(
                    node, site.source_epoch, "public-content", site.content_generation
                )
                for node in self.config.frontends
            ],
        )

    async def retry(self, actor_id: UUID) -> ContentSyncState:
        from app.modules.media.locking import lock_references

        await lock_references(self.session)
        site = await SettingsService(self.session, self.config).site(locked=True)
        release = await self.session.get(
            ContentRelease, (site.source_epoch, site.content_generation)
        )
        if release is None:
            release = await self.stage()
        else:
            await self.enqueue(release)
        events = await self.session.scalars(
            select(SettingsDeliveryEvent)
            .where(
                SettingsDeliveryEvent.source_epoch == release.source_epoch,
                SettingsDeliveryEvent.resource == "public-content",
                SettingsDeliveryEvent.revision == release.revision,
            )
            .with_for_update()
        )
        for event in events:
            event.applied_at = event.lease_id = event.lease_until = event.error = None
            event.attempts = 0
            event.next_attempt_at = datetime.now(UTC)
        self.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="public-content",
                revision=release.revision,
                changes={"action": "retry", "source_epoch": str(release.source_epoch)},
            )
        )
        await self.session.commit()
        return await self.state()
