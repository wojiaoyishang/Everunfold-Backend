"""Versioned, browser-authorized language membership for authored pages only."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure
from app.modules.content.component_document import ComponentDocument
from app.modules.content.content_paths import initial_path
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.pages.groups import members, translations
from app.modules.pages.models import ManagedPage, PageTranslationGroup
from app.modules.pages.schemas import (
    CreatePageTranslation,
    LinkPageTranslation,
    PageTranslationCatalog,
    PageTranslationMutation,
    PageValue,
)
from app.modules.pages.service import PagesService
from app.modules.pages.templates import page_data, page_template
from app.modules.pages.versions import record_page_version
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.languages import require_language
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService


class PageTranslations:
    def __init__(self, service: PagesService) -> None:
        self.service, self.session = service, service.session

    async def mutate(
        self,
        action: Literal["create", "link", "unlink", "review"],
        actor: User,
        payload: PageTranslationMutation,
    ) -> PageTranslationCatalog:
        await lock_references(self.session)
        await SettingsService(self.session, self.service.config).site(locked=True)
        operation = "page_translation_" + action
        cached = await self.service.accounts.operations.read(
            payload.operation_id, actor.id, operation, payload
        )
        if cached is not None:
            return PageTranslationCatalog.model_validate(cached)
        row = await self.session.get(ManagedPage, payload.id, populate_existing=True)
        if row is None:
            raise AuthFailure(404, 40496, "页面不存在")
        catalog = await translations(self.session, row)
        group = await self.session.get(PageTranslationGroup, catalog.id)
        assert group is not None
        if group.revision != payload.expected_revision:
            raise AuthFailure(409, 40995, "语言关联已变化，请刷新后重试")
        if row.deleted_at is not None:
            raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
        rows = await members(self.session, group.id)
        source = next(item for item in rows if item.id == group.source_id)
        if action == "create":
            assert isinstance(payload, CreatePageTranslation)
            language = await require_language(self.session, payload.language)
            if any(item.language == language.code for item in rows):
                raise AuthFailure(409, 40994, "此语言已有页面，请编辑或恢复已有译文")
            identity = uuid4()
            path = await initial_path(
                self.session, source.data["title"], identity, language=language.code
            )
            template = (
                await page_template(self.session, source.template_id)
                if source.template_id
                else None
            )
            value = (
                PageValue.model_validate(source.data)
                if payload.copy_source
                else PageValue(title=source.data["title"], path=path)
            )
            if not payload.copy_source and template is None:
                source_value = PageValue.model_validate(source.data)
                value.layout = source_value.layout
                value.edit_mode = source_value.edit_mode
                if value.edit_mode == "components":
                    value.component_document = ComponentDocument(version=1, nodes=[])
            value.path, value.enabled, value.javascript_enabled = path, False, False
            value.seo_title = value.seo_description = value.seo_canonical_path = ""
            value.seo_share_title = value.seo_share_description = value.seo_image_alt = ""
            translated = ManagedPage(
                id=identity,
                source="custom",
                type_id=source.type_id,
                template_id=source.template_id,
                revision=1,
                language=language.code,
                translation_group_id=group.id,
                translated_from_revision=source.applied_revision or source.revision,
                data=page_data(value, template),
                updated_at=datetime.now(UTC),
            )
            self.session.add(translated)
            record_page_version(self.session, translated, actor, "translate")
        elif action == "link":
            assert isinstance(payload, LinkPageTranslation)
            target = await self.session.get(ManagedPage, payload.target_id, populate_existing=True)
            if target is None or target.deleted_at is not None or target.source != "custom":
                raise AuthFailure(422, 42297, "只能关联未回收的自定义页面")
            if target.translation_group_id == group.id or any(
                item.language == target.language for item in rows
            ):
                raise AuthFailure(409, 40994, "此语言已有页面，请编辑或恢复已有译文")
            old = await self.session.get(PageTranslationGroup, target.translation_group_id)
            if old is None or len(await members(self.session, old.id)) != 1:
                raise AuthFailure(409, 40995, "目标页面已关联其他语言，请先解除关联")
            target.translation_group_id = group.id
            target.translated_from_revision = source.applied_revision or source.revision
            await self.session.delete(old)
        elif action == "unlink":
            if len(rows) < 2:
                raise AuthFailure(422, 42297, "此页面没有其他语言关联")
            if row.id == group.source_id:
                replacement = next(
                    (item for item in rows if item.id != row.id and item.deleted_at is None),
                    next(item for item in rows if item.id != row.id),
                )
                group.source_id = replacement.id
                replacement.translated_from_revision = None
                await self.session.flush()
            separate = PageTranslationGroup(id=uuid4(), source_id=row.id, revision=1)
            self.session.add(separate)
            row.translation_group_id, row.translated_from_revision = separate.id, None
        elif row.id == group.source_id:
            raise AuthFailure(422, 42297, "原文无需标记译文已核对")
        else:
            row.translated_from_revision = source.applied_revision or source.revision
        group.revision += 1
        await self.session.flush()
        # A membership change must reach readers with the same durable full-snapshot mechanism.
        if action in {"link", "unlink"}:
            await ContentPublication(self.session, self.service.config).stage(
                CacheChange("translations_changed")
            )
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page-languages:" + str(group.id),
                revision=group.revision,
                changes={"action": action, "page_id": str(row.id)},
            )
        )
        result = await translations(self.session, row)
        await self.service.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            operation,
            payload,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result
