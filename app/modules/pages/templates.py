"""Reusable custom source and transactional updates of its published pages."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.models import User
from app.modules.auth.permissions import user_permissions
from app.modules.auth.service import AuthFailure
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.pages.component_values import check_component_sources, page_document
from app.modules.pages.groups import source_value
from app.modules.pages.models import ManagedPage, PageTemplate
from app.modules.pages.schemas import (
    TEMPLATE_TYPE_PREFIX,
    PageCode,
    PageTemplateItem,
    PageTemplateReference,
    PageValue,
    SavePageTemplate,
    TitledPageCode,
    template_type_id,
)
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService


def template_identity(type_id: str) -> UUID | None:
    if not type_id.startswith(TEMPLATE_TYPE_PREFIX):
        return None
    suffix = type_id.removeprefix(TEMPLATE_TYPE_PREFIX)
    try:
        identity = UUID(hex=suffix)
    except ValueError:
        return None
    return identity if suffix == identity.hex else None


async def page_template(session: AsyncSession, identity: UUID) -> PageTemplate:
    template = await session.get(PageTemplate, identity)
    if template is None:
        raise AuthFailure(404, 40497, "页面模板不存在。")
    return template


def template_reference(template: PageTemplate) -> PageTemplateReference:
    return PageTemplateReference(id=template.id, title=template.title, revision=template.revision)


def page_data(value: PageValue, template: PageTemplate | None) -> dict[str, Any]:
    # Child drafts and applied settings keep their own fields. The source is read from the template.
    data = value.model_dump(mode="json", exclude=set(PageCode.model_fields) if template else None)
    if template is not None:
        data["layout"] = value.layout
    return data


def effective_value(data: dict[str, Any], template: PageTemplate | None) -> PageValue:
    # Older applied snapshots also contain the public record's identity and revision.
    if template is None:
        return source_value(data)
    code = PageCode.model_validate(
        {key: value for key, value in template.code.items() if key in PageCode.model_fields}
    )
    source = {
        **code.model_dump(),
        "edit_mode": code.edit_mode,
        "component_document": code.component_document,
    }
    return source_value({"layout": template.code.get("layout", "wide"), **data, **source})


def require_page_size(value: PageValue | TitledPageCode) -> None:
    if len(value.model_dump_json().encode()) > 512 * 1024:
        raise AuthFailure(422, 42296, "页面内容过大")


class PageTemplates:
    def __init__(self, accounts: AccountsService, config: Settings) -> None:
        self.accounts, self.session, self.config = accounts, accounts.session, config

    @staticmethod
    def item(template: PageTemplate) -> PageTemplateItem:
        return PageTemplateItem(
            id=template.id,
            type_id=template_type_id(template.id),
            revision=template.revision,
            value=TitledPageCode(
                **template.code, title=template.title, description=template.description
            ),
        )

    async def save(self, actor: User, payload: SavePageTemplate) -> PageTemplateItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_template_save", payload
        )
        if cached is not None:
            return PageTemplateItem.model_validate(cached)
        template = await self.session.get(PageTemplate, payload.id, populate_existing=True)
        if template is None and payload.expected_revision:
            raise AuthFailure(404, 40497, "页面模板不存在。")
        if template is not None and template.revision != payload.expected_revision:
            raise AuthFailure(409, 40992, "页面模板已被修改，请刷新后合并输入。")
        value = payload.value
        previous_value = (
            TitledPageCode(**template.code, title=template.title, description=template.description)
            if template
            else None
        )
        check_component_sources(value, previous_value)
        if value.edit_mode == "components":
            value.component_document = page_document(value)
        elif value.component_document is None and previous_value:
            value.component_document = previous_value.component_document
        if actor.role != "admin" and (
            value.javascript_enabled or (template and template.code.get("javascript_enabled"))
        ):
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        require_page_size(value)
        code = value.model_dump(mode="json", exclude={"title", "description"})
        previous_code = template.code if template else code
        if template is None:
            template = PageTemplate(id=payload.id, revision=0)
            self.session.add(template)
        template.title, template.description, template.code = value.title, value.description, code
        template.revision += 1
        if code != previous_code:
            await self.update_pages(actor, template)
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page-template:" + str(template.id),
                revision=template.revision,
                changes={"action": "save"},
            )
        )
        result = self.item(template)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            "page_template_save",
            payload,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def update_pages(self, actor: User, template: PageTemplate) -> None:
        rows = list(
            await self.session.scalars(
                select(ManagedPage).where(ManagedPage.template_id == template.id)
            )
        )
        if any(row.published for row in rows) and "pages:publish" not in user_permissions(actor):
            raise AuthFailure(403, 40399, "更新已发布页面使用的模板需要页面发布权限。")
        from app.modules.pages.service import PagesService

        service = PagesService(self.accounts, self.config)
        changes = []
        now = datetime.now(UTC)
        for row in rows:
            require_page_size(effective_value(row.data, template))
            if row.applied_snapshot is not None:
                require_page_size(effective_value(row.applied_snapshot, template))
            if row.published is None:
                continue
            row.revision += 1
            row.updated_at = now
            value = effective_value(row.applied_snapshot or row.published, template)
            row.published = await service.public_value(
                row, value, None, row.published.get("share_image_url")
            )
            changes.append(CacheChange("page_changed", row.id, (row.published["path"],)))
        if changes:
            await ContentPublication(self.session, self.config).stage(*changes)
