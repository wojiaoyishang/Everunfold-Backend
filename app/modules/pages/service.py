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
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.pages.groups import promote_original, source_value, translations, unapplied
from app.modules.pages.models import FrontendPageRegistration, ManagedPage, PageTranslationGroup
from app.modules.pages.paths import content_path_taken, page_path_taken
from app.modules.pages.schemas import (
    ManagedPageCatalog,
    ManagedPageItem,
    ManagedPageSummary,
    PageRegistration,
    PageValue,
    PublicManagedPage,
    RemoveManagedPage,
    SaveManagedPage,
    SetPageEnabled,
)
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

    async def registration(self, identity: str | None) -> tuple[PageRegistration | None, bool]:
        if identity is None:
            return None, True
        rows = (
            await self.session.scalars(
                select(FrontendPageRegistration)
                .where(FrontendPageRegistration.page_id == identity)
                .order_by(FrontendPageRegistration.node_id)
            )
        ).all()
        if not rows:
            return None, False
        nodes = {key for key, value in self.config.frontends.items() if value.enabled}
        active = [row for row in rows if row.node_id in nodes]
        definitions = [PageRegistration.model_validate(row.definition) for row in active or rows]
        available = (
            bool(nodes)
            and nodes.issubset({row.node_id for row in active})
            and all(value == definitions[0] for value in definitions)
        )
        site = await SettingsService(self.session, self.config).site()
        from app.modules.settings.defaults import public_settings
        from app.modules.settings.extensions_service import catalog as extension_catalog

        disabled = set(public_settings(site.data).extensions.disabled_plugins)
        if disabled:
            inventory = await extension_catalog(self.session, self.config)
            if any(
                plugin.id in disabled and identity in plugin.page_ids
                for node in inventory.nodes
                if node.inventory
                for plugin in node.inventory.plugins
            ):
                available = False
        return definitions[0], available

    async def item(self, row: ManagedPage) -> ManagedPageItem:
        registration, available = await self.registration(row.implementation_id)
        group = await translations(self.session, row) if row.source == "custom" else None
        return ManagedPageItem(
            id=row.id,
            source=cast(Literal["custom", "registered"], row.source),
            implementation_id=row.implementation_id,
            revision=row.revision,
            applied_revision=row.applied_revision,
            enabled=row.published is not None,
            value=PageValue.model_validate(row.data),
            registration=registration,
            available=available,
            updated_at=row.updated_at,
            archived=row.deleted_at is not None,
            has_applied_snapshot=row.applied_snapshot is not None,
            has_unapplied_changes=unapplied(row),
            applied_javascript_enabled=bool(
                (row.applied_snapshot or row.data).get("javascript_enabled")
            ),
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
        items = []
        for row in rows:
            item = await self.item(row)
            if not item.is_original or (item.source == "registered" and not item.available):
                continue
            items.append(
                ManagedPageSummary(
                    **item.model_dump(exclude={"value", "registration"}),
                    title=item.value.title,
                    path=item.value.path,
                    description=item.value.description,
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
            raise AuthFailure(422, 42296, "页面地址与语言入口冲突")
        if await page_path_taken(self.session, path, identity) or await content_path_taken(
            self.session, path
        ):
            raise AuthFailure(409, 40997, "该地址已被其他内容或页面使用")

    async def save(self, actor: User, payload: SaveManagedPage) -> ManagedPageItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        fingerprint = payload.model_dump(
            mode="json", exclude={"language"} if payload.language is None else set()
        )
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_save", fingerprint
        )
        if cached is not None:
            return ManagedPageItem.model_validate(cached)
        row = await self.session.get(ManagedPage, payload.id, populate_existing=True)
        new_group = None
        if row is None:
            if payload.expected_revision:
                raise AuthFailure(404, 40496, "页面不存在")
            language = await require_language(self.session, payload.language)
            group = PageTranslationGroup(id=uuid4(), source_id=payload.id, revision=1)
            new_group = group
            row = ManagedPage(
                id=payload.id,
                source="custom",
                revision=1,
                implementation_id=None,
                data={},
                updated_at=datetime.now(UTC),
                language=language.code,
                translation_group_id=group.id,
            )
        else:
            row = await self.target(payload.id, payload.expected_revision)
            if row.deleted_at is not None:
                raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
            if payload.language is not None and payload.language != row.language:
                raise AuthFailure(422, 42297, "请通过语言关联创建独立译文，不可更改已有页面的语言")
        next_revision = row.revision + (0 if payload.expected_revision == 0 else 1)
        value = payload.value.model_copy(deep=True)
        definition, available = await self.registration(row.implementation_id)
        if row.source == "registered":
            if (
                value.html
                or value.css
                or value.javascript
                or value.javascript_enabled
                or value.javascript_position != "body_end"
            ):
                raise AuthFailure(403, 40396, "代码注册页面只允许修改已暴露的选项")
            if payload.apply and value.enabled and not available:
                raise AuthFailure(409, 40998, "前端页面注册缺失或节点版本不一致")
        if value.javascript_enabled and actor.role != "admin":
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        if len(value.model_dump_json().encode()) > 512 * 1024:
            raise AuthFailure(422, 42296, "页面内容过大")
        await self.validate_path(value.path, row.id)
        row.revision = next_revision
        if new_group is not None:
            self.session.add(new_group)
        self.session.add(row)
        row.data, row.updated_at = value.model_dump(mode="json"), datetime.now(UTC)
        if payload.apply:
            if "pages:publish" not in user_permissions(actor):
                raise AuthFailure(403, 40364, "没有执行此操作的权限")
            row.applied_revision = row.revision
            row.applied_snapshot = value.model_copy(update={"enabled": True}).model_dump(
                mode="json"
            )
            row.published = self.public_value(row, value, definition) if value.enabled else None
            await self.session.flush()
            await ContentPublication(self.session, self.config).stage()
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

    @staticmethod
    def public_value(
        row: ManagedPage, value: PageValue, definition: PageRegistration | None
    ) -> dict[str, Any]:
        return PublicManagedPage(
            **{**value.model_dump(), "enabled": True},
            id=row.id,
            source=cast(Literal["custom", "registered"], row.source),
            revision=row.applied_revision or row.revision,
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
        value = source_value(row.applied_snapshot or row.data)
        if value.javascript_enabled and actor.role != "admin":
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        definition, available = await self.registration(row.implementation_id)
        if payload.enabled:
            if row.source == "registered" and not available:
                raise AuthFailure(409, 40998, "前端页面注册缺失或节点版本不一致")
            await self.validate_path(value.path, row.id)
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        row.data = {**row.data, "enabled": payload.enabled}
        if row.applied_snapshot is None and payload.enabled:
            row.applied_snapshot = value.model_copy(update={"enabled": True}).model_dump(
                mode="json"
            )
            row.applied_revision = row.revision
        row.published = self.public_value(row, value, definition) if payload.enabled else None
        await self.session.flush()
        await ContentPublication(self.session, self.config).stage()
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
        if row.source == "registered":
            raise AuthFailure(403, 40396, "代码注册页面可以停用，不能移除代码登记")
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
        await ContentPublication(self.session, self.config).stage()
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
    ]
    if not authorized:
        for page in pages:
            if required(captcha, "read", "page", page.model_dump()):
                page.access = "captcha"
                page.description = ""
                page.html, page.css, page.javascript = "", "", ""
                page.javascript_enabled = False
                page.options = {}
    return pages
