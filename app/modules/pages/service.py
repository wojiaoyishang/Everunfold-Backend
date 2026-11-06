"""Versioned page drafts and public releases share existing durable content delivery."""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.models import User
from app.modules.auth.permissions import user_permissions
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import Content
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.pages.models import FrontendPageRegistration, ManagedPage
from app.modules.pages.options import validate_options
from app.modules.pages.schemas import (
    ManagedPageCatalog,
    ManagedPageItem,
    ManagedPageSummary,
    PageRegistration,
    PageValue,
    PublicManagedPage,
    RemoveManagedPage,
    SaveManagedPage,
)
from app.modules.settings.languages import language_catalog, reserved_language_path
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
        definitions = [PageRegistration.model_validate(row.definition) for row in rows]
        nodes = {key for key, value in self.config.frontends.items() if value.enabled}
        available = (
            bool(nodes)
            and nodes.issubset({row.node_id for row in rows})
            and all(value == definitions[0] for value in definitions)
        )
        return definitions[0], available

    async def item(self, row: ManagedPage) -> ManagedPageItem:
        registration, available = await self.registration(row.implementation_id)
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
        if (
            await self.session.scalar(
                select(ManagedPage.id)
                .where(
                    ManagedPage.id != identity,
                    ManagedPage.deleted_at.is_(None),
                    or_(
                        ManagedPage.data["path"].astext == path,
                        ManagedPage.published["path"].astext == path,
                    ),
                )
                .limit(1)
            )
            or await self.session.scalar(select(Content.id).where(Content.path == path).limit(1))
            or await self.session.scalar(
                select(ContentDraft.id)
                .where(
                    or_(
                        ContentDraft.public_path == path,
                        ContentDraft.editor_settings["path"].astext == path,
                    )
                )
                .limit(1)
            )
        ):
            raise AuthFailure(409, 40997, "该地址已被其他内容或页面使用")

    async def save(self, actor: User, payload: SaveManagedPage) -> ManagedPageItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_save", payload
        )
        if cached is not None:
            return ManagedPageItem.model_validate(cached)
        row = await self.session.get(ManagedPage, payload.id, populate_existing=True)
        if row is None:
            if payload.expected_revision:
                raise AuthFailure(404, 40496, "页面不存在")
            row = ManagedPage(
                id=payload.id,
                source="custom",
                revision=1,
                implementation_id=None,
                data={},
                updated_at=datetime.now(UTC),
            )
        else:
            row = await self.target(payload.id, payload.expected_revision)
            if row.deleted_at is not None:
                raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
        next_revision = row.revision + (0 if payload.expected_revision == 0 else 1)
        value = payload.value.model_copy(deep=True)
        definition, available = await self.registration(row.implementation_id)
        if row.source == "registered":
            if value.html or value.css or value.javascript or value.javascript_enabled:
                raise AuthFailure(403, 40396, "代码注册页面只允许修改已暴露的选项")
            if payload.apply and value.enabled and not available:
                raise AuthFailure(409, 40998, "前端页面注册缺失或节点版本不一致")
            try:
                value.options = (
                    validate_options(definition.fields, value.options)
                    if definition
                    else value.options
                )
            except ValueError as error:
                raise AuthFailure(422, 42296, "页面选项格式无效") from error
        elif value.options:
            raise AuthFailure(422, 42296, "自定义页面不接受注册选项")
        if value.javascript_enabled:
            if actor.role != "admin":
                raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
            await self.accounts.confirm_password(actor, payload.current_password)
        if len(value.model_dump_json().encode()) > 512 * 1024:
            raise AuthFailure(422, 42296, "页面内容过大")
        await self.validate_path(value.path, row.id)
        row.revision = next_revision
        self.session.add(row)
        row.data, row.updated_at = value.model_dump(mode="json"), datetime.now(UTC)
        if payload.apply:
            if "pages:publish" not in user_permissions(actor):
                raise AuthFailure(403, 40364, "没有执行此操作的权限")
            row.applied_revision = row.revision
            row.published = (
                PublicManagedPage(
                    **value.model_dump(),
                    id=row.id,
                    source=cast(Literal["custom", "registered"], row.source),
                    revision=row.revision,
                    implementation_id=row.implementation_id,
                    implementation_version=definition.version if definition else None,
                ).model_dump(mode="json")
                if value.enabled
                else None
            )
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
        row.deleted_at = None if payload.restore else datetime.now(UTC)
        row.data = {**row.data, "enabled": False}
        row.published = None
        row.revision += 1
        row.updated_at = datetime.now(UTC)
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


async def public_pages(session: AsyncSession) -> list[PublicManagedPage]:
    rows = (
        await session.scalars(
            select(ManagedPage)
            .where(ManagedPage.deleted_at.is_(None), ManagedPage.published.is_not(None))
            .order_by(ManagedPage.id)
        )
    ).all()
    return [PublicManagedPage.model_validate(row.published) for row in rows]
