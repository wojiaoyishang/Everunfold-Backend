"""Versioned page drafts and public releases share existing durable content delivery."""

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.models import User
from app.modules.auth.permissions import user_permissions
from app.modules.auth.service import AuthFailure
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.pages.component_values import (
    check_component_sources,
    page_document,
    public_page_value,
)
from app.modules.pages.groups import promote_original, source_value, translations, unapplied
from app.modules.pages.models import ManagedPage, PageTemplate, PageTranslationGroup
from app.modules.pages.options import bounded_options
from app.modules.pages.paths import content_path_taken, normalize_page_path, page_path_taken
from app.modules.pages.schemas import (
    CUSTOM_PAGE_TYPE,
    TEMPLATE_TYPE_PREFIX,
    ManagedPageCatalog,
    ManagedPageItem,
    ManagedPageSummary,
    PageRegistration,
    PageValue,
    PublicManagedPage,
    RemoveManagedPage,
    RestorePageVersion,
    SaveManagedPage,
    SetPageEnabled,
    template_type_id,
)
from app.modules.pages.templates import (
    effective_value,
    page_data,
    page_template,
    require_page_size,
    template_identity,
    template_reference,
)
from app.modules.pages.types import CUSTOM_PAGE_TITLE, RegisteredPageType, registered_types
from app.modules.pages.versions import get_version, record_page_version
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.languages import (
    language_catalog,
    require_language,
    reserved_language_path,
)
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService


class PagesService:
    def __init__(self, accounts: AccountsService, config: Settings) -> None:
        self.accounts, self.session, self.config = accounts, accounts.session, config

    async def registration(self, identity: str) -> tuple[PageRegistration | None, bool]:
        if identity == CUSTOM_PAGE_TYPE:
            return None, True
        registered = (await registered_types(self.session, self.config, identity)).get(identity)
        return (registered.definition, registered.available) if registered else (None, False)

    async def item(
        self,
        row: ManagedPage,
        types: dict[str, RegisteredPageType] | None = None,
        templates: dict[UUID, PageTemplate] | None = None,
    ) -> ManagedPageItem:
        if types is None:
            registration, available = await self.registration(row.type_id)
        else:
            registered = types.get(row.type_id)
            registration = registered.definition if registered else None
            available = row.type_id == CUSTOM_PAGE_TYPE or bool(registered and registered.available)
        template = (
            (
                templates[row.template_id]
                if templates is not None
                else await page_template(self.session, row.template_id)
            )
            if row.template_id is not None
            else None
        )
        group = await translations(self.session, row) if row.source == "custom" else None
        value = effective_value(row.data, template)
        media_urls = (
            await editor_media(
                self.session,
                self.config,
                value.model_dump_json(include={"component_document", "component_parameters"}),
                EditorSettings(),
                strict=False,
            )
            if types is None and value.component_document is not None
            else {}
        )
        return ManagedPageItem(
            media_urls=media_urls,
            id=row.id,
            source=cast(Literal["custom", "registered"], row.source),
            implementation_id=row.implementation_id,
            type_id=template_type_id(template.id) if template else row.type_id,
            template=template_reference(template) if template else None,
            revision=row.revision,
            applied_revision=row.applied_revision,
            enabled=row.published is not None,
            value=value,
            share_image_url=await self.share_image(value, strict=False) if types is None else None,
            registration=registration,
            available=available,
            updated_at=row.updated_at,
            archived=row.deleted_at is not None,
            has_applied_snapshot=row.applied_snapshot is not None,
            has_unapplied_changes=unapplied(row),
            applied_javascript_enabled=effective_value(
                row.applied_snapshot or row.data, template
            ).javascript_enabled,
            language=row.language,
            translation_group_id=row.translation_group_id,
            is_original=group is None or group.source_id == row.id,
            translations=group,
        )

    async def catalog(self) -> ManagedPageCatalog:
        rows = (
            await self.session.scalars(
                select(ManagedPage).order_by(
                    ManagedPage.source.desc(), ManagedPage.updated_at.desc(), ManagedPage.id
                )
            )
        ).all()
        types = await registered_types(self.session, self.config)
        templates = {
            template.id: template for template in await self.session.scalars(select(PageTemplate))
        }
        items = []
        for row in rows:
            item = await self.item(row, types, templates)
            if not item.is_original:
                continue
            items.append(
                ManagedPageSummary(
                    **item.model_dump(include=set(ManagedPageSummary.model_fields)),
                    title=item.value.title,
                    path=item.value.path,
                    description=item.value.description,
                    type_title=(
                        item.template.title
                        if item.template
                        else item.registration.title
                        if item.registration
                        else CUSTOM_PAGE_TITLE
                        if item.source == "custom"
                        else item.type_id
                    ),
                )
            )
        return ManagedPageCatalog(items=items)

    async def target(self, identity: UUID, expected: int) -> ManagedPage:
        row = await self.session.get(ManagedPage, identity, populate_existing=True)
        if row is None:
            raise AuthFailure(404, 40496, "页面不存在")
        if row.revision != expected:
            raise AuthFailure(409, 40996, "页面已被修改，请刷新后合并输入")
        return row

    async def validate_path(self, path: str, identity: UUID) -> None:
        if reserved_language_path(path, await language_catalog(self.session)):
            raise AuthFailure(422, 42209, "页面地址与语言入口冲突")
        if await page_path_taken(self.session, path, identity) or await content_path_taken(
            self.session, path
        ):
            raise AuthFailure(409, 40997, "该地址已被其他内容或页面使用")

    async def save(self, actor: User, payload: SaveManagedPage) -> ManagedPageItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        fingerprint = payload.model_dump(
            mode="json",
            exclude={
                key
                for key, value in (("language", payload.language), ("type_id", payload.type_id))
                if value is None
            },
        )
        # Keep fingerprints of pre-template page operations with explicit paths replayable.
        if payload.value.path is not None:
            fingerprint["value"]["path"] = normalize_page_path(payload.value.path)
        if "layout" in payload.value.model_fields_set:
            fingerprint["value"]["layout"] = payload.value.layout
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_save", fingerprint
        )
        if cached is not None:
            return ManagedPageItem.model_validate(cached)
        row = await self.session.get(ManagedPage, payload.id, populate_existing=True)
        template = None
        new_group = None
        creating = row is None
        if row is None:
            if payload.expected_revision:
                raise AuthFailure(404, 40496, "页面不存在")
            type_id = payload.type_id or CUSTOM_PAGE_TYPE
            template_id = template_identity(type_id)
            if template_id is not None:
                template = await page_template(self.session, template_id)
            elif type_id.startswith(TEMPLATE_TYPE_PREFIX):
                raise AuthFailure(422, 42294, "页面类型不存在")
            custom = type_id == CUSTOM_PAGE_TYPE or template is not None
            language = None
            if custom:
                language = (await require_language(self.session, payload.language)).code
                new_group = PageTranslationGroup(id=uuid4(), source_id=payload.id, revision=1)
            elif payload.language is not None:
                raise AuthFailure(422, 42297, "插件页面的多语言由代码管理")
            row = ManagedPage(
                id=payload.id,
                source="custom" if custom else "registered",
                revision=1,
                type_id=CUSTOM_PAGE_TYPE if custom else type_id,
                template_id=template_id,
                data={},
                updated_at=datetime.now(UTC),
                language=language,
                translation_group_id=new_group.id if new_group else None,
            )
        else:
            row = await self.target(payload.id, payload.expected_revision)
            if row.deleted_at is not None:
                raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
            if row.template_id is not None:
                template = await page_template(self.session, row.template_id)
            type_id = template_type_id(template.id) if template else row.type_id
            if payload.type_id is not None and payload.type_id != type_id:
                raise AuthFailure(422, 42294, "已保存页面的类型不可更改")
            if payload.language is not None and payload.language != row.language:
                raise AuthFailure(422, 42297, "请通过语言关联创建独立译文，不可更改已有页面的语言")
        next_revision = row.revision + (0 if payload.expected_revision == 0 else 1)
        path = fingerprint["value"]["path"]
        if path is None:
            if creating:
                from app.modules.content.content_paths import initial_path

                catalog = await language_catalog(self.session)
                path = await initial_path(
                    self.session,
                    payload.value.title,
                    row.id,
                    language=row.language or catalog.default_code,
                    type_id="page",
                )
            else:
                path = row.data["path"]
        submitted = payload.value.model_dump()
        if "layout" in payload.value.model_fields_set:
            submitted["layout"] = payload.value.layout
        elif template and "layout" in row.data:
            submitted["layout"] = row.data["layout"]
        value = effective_value({**submitted, "path": path}, template)
        if template is None and row.source == "custom":
            previous_value = effective_value(row.data, None) if not creating else None
            check_component_sources(value, previous_value)
            if value.edit_mode == "components":
                value.component_document = page_document(value)
            elif value.component_document is None and previous_value:
                value.component_document = previous_value.component_document
        definition, available = await self.registration(row.type_id)
        if row.source == "registered":
            if creating and definition is None:
                raise AuthFailure(422, 42294, "页面类型不存在")
            if creating and definition is not None:
                try:
                    value.options = bounded_options(definition.initial_options() | value.options)
                except ValueError as exc:
                    raise AuthFailure(422, 42296, "页面配置过大或嵌套过深") from exc
            if (
                value.edit_mode != "source"
                or value.component_document is not None
                or value.component_parameters
                or value.html
                or value.css
                or value.component_css
                or value.component_css_override
                or value.javascript
                or value.javascript_enabled
                or value.javascript_position != "body_end"
            ):
                raise AuthFailure(403, 40396, "插件页面只允许修改页面选项")
            if payload.apply and value.enabled and not available:
                raise AuthFailure(409, 40998, "前端页面注册缺失或节点版本不一致")
            if definition and not definition.seo_editable:
                previous = effective_value(row.data, None) if not creating else None
                if value.seo_overrides() != (
                    previous.seo_overrides() if previous else {}
                ) or value.share_image_media_id != (
                    previous.share_image_media_id if previous else None
                ):
                    raise AuthFailure(403, 40396, "该页面类型不允许修改搜索与分享设置")
        if value.edit_mode == "source" and value.javascript_enabled and actor.role != "admin":
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        require_page_size(value)
        await self.validate_path(value.path, row.id)
        share_image = await self.share_image(value, strict=True)
        row.revision = next_revision
        if new_group is not None:
            self.session.add(new_group)
        self.session.add(row)
        row.data, row.updated_at = page_data(value, template), datetime.now(UTC)
        if payload.apply:
            if "pages:publish" not in user_permissions(actor):
                raise AuthFailure(403, 40364, "没有执行此操作的权限")
            row.applied_revision = row.revision
            row.applied_snapshot = page_data(value.model_copy(update={"enabled": True}), template)
            row.published = (
                await self.public_value(row, value, definition, share_image)
                if value.enabled
                else None
            )
            await self.session.flush()
            await ContentPublication(self.session, self.config).stage(CacheChange("page_changed"))
        record_page_version(self.session, row, actor, "apply" if payload.apply else "save")
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page:" + str(row.id),
                revision=row.revision,
                changes={"action": "apply" if payload.apply else "save"},
            )
        )
        result = await self.item(row)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            "page_save",
            fingerprint,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def restore_version(
        self, actor: User, identity: UUID, payload: RestorePageVersion
    ) -> ManagedPageItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        operation = "page_version_restore"
        fingerprint = {"id": str(identity), **payload.model_dump(mode="json")}
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, operation, fingerprint
        )
        if cached is not None:
            return ManagedPageItem.model_validate(cached)
        row = await self.target(identity, payload.expected_revision)
        if row.deleted_at is not None:
            raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
        version = await get_version(self.session, "page", identity, payload.revision)
        template = await page_template(self.session, row.template_id) if row.template_id else None
        previous = effective_value(row.data, template)
        # A linked page owns its settings and parameters. Its source follows the live template.
        snapshot = source_value(version.value)
        value = effective_value(page_data(snapshot, template), template)
        value.enabled = previous.enabled
        if actor.role != "admin" and (value.javascript_enabled or previous.javascript_enabled):
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        definition, _ = await self.registration(row.type_id)
        if (
            definition
            and not definition.seo_editable
            and (
                value.seo_overrides() != previous.seo_overrides()
                or value.share_image_media_id != previous.share_image_media_id
            )
        ):
            raise AuthFailure(403, 40396, "该页面类型不允许修改搜索与分享设置")
        require_page_size(value)
        await self.validate_path(value.path, row.id)
        await self.share_image(value, strict=True)
        row.revision += 1
        row.data, row.updated_at = page_data(value, template), datetime.now(UTC)
        record_page_version(
            self.session, row, actor, "restore_draft", from_revision=version.revision
        )
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource=f"page:{row.id}",
                revision=row.revision,
                changes={"action": "restore_draft", "from_revision": version.revision},
            )
        )
        result = await self.item(row)
        await self.accounts.operations.remember(
            payload.operation_id, actor.id, operation, fingerprint, result.model_dump(mode="json")
        )
        await self.session.commit()
        return result

    async def share_image(self, value: PageValue, *, strict: bool) -> str | None:
        if value.share_image_media_id is None:
            return None
        images = await editor_media(
            self.session,
            self.config,
            "",
            EditorSettings(share_image_media_id=value.share_image_media_id),
            strict=strict,
        )
        image = images.get(str(value.share_image_media_id))
        return image.original if image else None

    async def public_value(
        self,
        row: ManagedPage,
        value: PageValue,
        definition: PageRegistration | None,
        image: str | None,
    ) -> dict[str, Any]:
        from app.modules.shortcodes.regions import RegionPublication, require_no_regions

        value = public_page_value(value, linked=row.template_id is not None)
        regions = RegionPublication(self.session, self.config, "page", row.id)
        require_no_regions(value.model_dump(mode="json", exclude={"html", "component_document"}))
        value = value.model_copy(
            update={
                "html": regions.redact(value.html, "html"),
                "component_document": regions.redact_document(value.component_document),
            }
        )
        await regions.store()
        media = (
            await editor_media(
                self.session,
                self.config,
                value.component_document.model_dump_json(),
                EditorSettings(),
            )
            if value.component_document
            else {}
        )
        return PublicManagedPage(
            **{**value.model_dump(exclude={"share_image_media_id"}), "enabled": True},
            share_image_url=image,
            media_urls=media,
            id=row.id,
            source=cast(Literal["custom", "registered"], row.source),
            revision=row.revision
            if row.template_id is not None
            else row.applied_revision or row.revision,
            implementation_id=row.implementation_id,
            implementation_version=definition.version if definition else None,
            language=row.language,
            translation_group_id=row.translation_group_id,
        ).model_dump(mode="json")

    async def set_enabled(self, actor: User, payload: SetPageEnabled) -> ManagedPageItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_enabled", payload
        )
        if cached is not None:
            return ManagedPageItem.model_validate(cached)
        row = await self.target(payload.id, payload.expected_revision)
        if row.deleted_at is not None:
            raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
        if "pages:publish" not in user_permissions(actor):
            raise AuthFailure(403, 40364, "没有执行此操作的权限")
        if payload.enabled and row.applied_snapshot is None and not payload.apply_draft:
            raise AuthFailure(409, 40993, "此页面尚无已应用内容，请确认应用当前草稿")
        template = await page_template(self.session, row.template_id) if row.template_id else None
        value = effective_value(row.applied_snapshot or row.data, template)
        if value.edit_mode == "source" and value.javascript_enabled and actor.role != "admin":
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        definition, available = await self.registration(row.type_id)
        if payload.enabled:
            if row.source == "registered" and not available:
                raise AuthFailure(409, 40998, "前端页面注册缺失或节点版本不一致")
            await self.validate_path(value.path, row.id)
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        row.data = {**row.data, "enabled": payload.enabled}
        if row.applied_snapshot is None and payload.enabled:
            row.applied_snapshot = page_data(value.model_copy(update={"enabled": True}), template)
            row.applied_revision = row.revision
        row.published = (
            await self.public_value(
                row, value, definition, await self.share_image(value, strict=True)
            )
            if payload.enabled
            else None
        )
        await self.session.flush()
        await ContentPublication(self.session, self.config).stage(CacheChange("page_changed"))
        record_page_version(self.session, row, actor, "enable" if payload.enabled else "disable")
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page:" + str(row.id),
                revision=row.revision,
                changes={"action": "enable" if payload.enabled else "disable"},
            )
        )
        result = await self.item(row)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            "page_enabled",
            payload,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def remove(self, actor: User, payload: RemoveManagedPage) -> ManagedPageItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_remove", payload
        )
        if cached is not None:
            return ManagedPageItem.model_validate(cached)
        row = await self.target(payload.id, payload.expected_revision)
        if "pages:publish" not in user_permissions(actor):
            raise AuthFailure(403, 40364, "没有执行此操作的权限")
        if payload.restore:
            await self.validate_path(row.data["path"], row.id)
        row.deleted_at = None if payload.restore else datetime.now(UTC)
        row.data = {**row.data, "enabled": False}
        row.published = None
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        await promote_original(self.session, row)
        await self.session.flush()
        await ContentPublication(self.session, self.config).stage(CacheChange("page_changed"))
        record_page_version(self.session, row, actor, "restore" if payload.restore else "remove")
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page:" + str(row.id),
                revision=row.revision,
                changes={"action": "restore" if payload.restore else "remove"},
            )
        )
        result = await self.item(row)
        await self.accounts.operations.remember(
            payload.operation_id, actor.id, "page_remove", payload, result.model_dump(mode="json")
        )
        await self.session.commit()
        return result


async def public_pages(
    session: AsyncSession, *, authorized: bool = False
) -> list[PublicManagedPage]:
    from app.modules.captcha.service import current_config, required
    from app.modules.pages.availability import disabled_page_ids

    disabled = await disabled_page_ids(session)
    captcha = await current_config(session)
    rows = (
        await session.scalars(
            select(ManagedPage)
            .where(ManagedPage.deleted_at.is_(None), ManagedPage.published.is_not(None))
            .order_by(ManagedPage.id)
        )
    ).all()
    sources = dict(
        (
            await session.execute(select(PageTranslationGroup.id, PageTranslationGroup.source_id))
        ).all()
    )
    pages = [
        PublicManagedPage.model_validate(
            {
                **(row.published or {}),
                "language": row.language,
                "translation_group_id": row.translation_group_id,
                "is_original": row.source == "registered"
                or (
                    row.translation_group_id is not None
                    and sources.get(row.translation_group_id) == row.id
                ),
            }
        )
        for row in rows
        if row.implementation_id not in disabled
    ]
    if not authorized:
        for page in pages:
            if required(captcha, "read", "page", page.model_dump()):
                page.access = "captcha"
                page.description = ""
                page.clear_seo()
                page.share_image_url = None
                page.html, page.css, page.javascript = "", "", ""
                page.component_css = ""
                page.component_css_override = ""
                page.javascript_enabled = False
                page.options = {}
                page.media_urls = {}
                page.component_document = None
                page.component_parameters = {}
    return pages
