"""Reusable custom source and transactional updates of its published pages."""

import hashlib
import json
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
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.pages.component_values import check_component_sources, page_document
from app.modules.pages.groups import source_value
from app.modules.pages.models import ManagedPage, PageTemplate
from app.modules.pages.schemas import (
    TEMPLATE_TYPE_PREFIX,
    ApplyPageTemplate,
    PageCode,
    PageTemplateItem,
    PageTemplateReference,
    PageValue,
    RestorePageVersion,
    RevisionAction,
    SavePageTemplate,
    TemplateAffectedPage,
    TemplateImpact,
    TitledPageCode,
    template_type_id,
)
from app.modules.pages.versions import get_version, record_page_version, record_version
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
    return PageTemplateReference(
        id=template.id, title=template.title, revision=template.applied_revision
    )


def applied_template(template: PageTemplate) -> TitledPageCode:
    return TitledPageCode(**template.code, title=template.title, description=template.description)


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

    async def item(self, template: PageTemplate) -> PageTemplateItem:
        applied = applied_template(template)
        draft = (
            TitledPageCode.model_validate(template.draft_value)
            if template.draft_value is not None
            else None
        )
        media_urls = {}
        for value in (applied, draft):
            if value is None or value.component_document is None:
                continue
            media_urls.update(
                await editor_media(
                    self.session,
                    self.config,
                    value.component_document.model_dump_json(),
                    EditorSettings(),
                    strict=False,
                )
            )
        return PageTemplateItem(
            media_urls=media_urls,
            id=template.id,
            type_id=template_type_id(template.id),
            revision=template.revision,
            applied_revision=template.applied_revision,
            value=draft or applied,
            applied_value=applied if draft is not None else None,
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
        if template is not None and template.draft_value is not None and payload.apply:
            raise AuthFailure(409, 40992, "请确认受影响的页面后应用模板。")
        value = payload.value
        previous_value = (
            TitledPageCode.model_validate(template.draft_value)
            if template is not None and template.draft_value is not None
            else applied_template(template)
            if template
            else None
        )
        check_component_sources(value, previous_value)
        if value.edit_mode == "components":
            value.component_document = page_document(value)
        elif value.component_document is None and previous_value:
            value.component_document = previous_value.component_document
        self.validate_source(actor, value, template)
        apply = template is None or payload.apply
        if template is None:
            template = PageTemplate(id=payload.id, revision=0)
            self.session.add(template)
        template.revision += 1
        if apply:
            await self.publish(actor, template, value)
        else:
            template.draft_value = value.model_dump(mode="json")
        return await self.finish(
            actor, template, value, "apply" if apply else "save", "page_template_save", payload
        )

    @staticmethod
    def validate_source(actor: User, value: TitledPageCode, template: PageTemplate | None) -> None:
        if actor.role != "admin" and (
            value.javascript_enabled
            or (
                template is not None
                and (
                    template.code.get("javascript_enabled")
                    or (template.draft_value or {}).get("javascript_enabled")
                )
            )
        ):
            raise AuthFailure(403, 40397, "只有管理员可以启用或修改页面脚本")
        require_page_size(value)

    async def affected_pages(self, identity: UUID) -> list[ManagedPage]:
        return list(
            await self.session.scalars(
                select(ManagedPage)
                .where(ManagedPage.template_id == identity, ManagedPage.deleted_at.is_(None))
                .order_by(ManagedPage.id)
            )
        )

    def impact(self, template: PageTemplate, rows: list[ManagedPage]) -> TemplateImpact:
        pages = [
            TemplateAffectedPage(
                id=row.id,
                title=(row.published or row.data)["title"],
                path=(row.published or row.data)["path"],
                language=row.language,
                published=row.published is not None,
            )
            for row in rows
        ]
        digest = hashlib.sha256(
            json.dumps(
                [
                    str(template.id),
                    template.revision,
                    [
                        (page.model_dump(mode="json"), row.revision)
                        for page, row in zip(pages, rows, strict=True)
                    ],
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return TemplateImpact(revision=template.revision, digest=digest, pages=pages)

    async def restore_version(
        self, actor: User, identity: UUID, payload: RestorePageVersion
    ) -> PageTemplateItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        operation = "page_template_restore"
        fingerprint = {"id": str(identity), **payload.model_dump(mode="json")}
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, operation, fingerprint
        )
        if cached is not None:
            return PageTemplateItem.model_validate(cached)
        template = await page_template(self.session, identity)
        if template.revision != payload.expected_revision:
            raise AuthFailure(409, 40992, "页面模板已被修改，请刷新后合并输入。")
        version = await get_version(self.session, "template", identity, payload.revision)
        value = TitledPageCode.model_validate(version.value)
        self.validate_source(actor, value, template)
        template.revision += 1
        template.draft_value = value.model_dump(mode="json")
        return await self.finish(
            actor,
            template,
            value,
            "restore_draft",
            operation,
            payload,
            from_revision=version.revision,
            fingerprint=fingerprint,
        )

    async def apply(
        self, actor: User, identity: UUID, payload: ApplyPageTemplate
    ) -> PageTemplateItem:
        await lock_references(self.session)
        await SettingsService(self.session, self.config).site(locked=True)
        operation = "page_template_apply"
        fingerprint = {"id": str(identity), **payload.model_dump(mode="json")}
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, operation, fingerprint
        )
        if cached is not None:
            return PageTemplateItem.model_validate(cached)
        template = await page_template(self.session, identity)
        rows = await self.affected_pages(identity)
        if (
            template.revision != payload.expected_revision
            or self.impact(template, rows).digest != payload.digest
        ):
            raise AuthFailure(409, 40988, "模板或受影响的页面已变化，请重新查看并确认。")
        if template.draft_value is None:
            raise AuthFailure(409, 40992, "模板没有待应用的草稿。")
        value = TitledPageCode.model_validate(template.draft_value)
        self.validate_source(actor, value, template)
        template.revision += 1
        await self.publish(actor, template, value, rows)
        return await self.finish(
            actor, template, value, "apply", operation, payload, fingerprint=fingerprint
        )

    async def finish(
        self,
        actor: User,
        template: PageTemplate,
        value: TitledPageCode,
        action: RevisionAction,
        operation: str,
        payload: SavePageTemplate | RestorePageVersion | ApplyPageTemplate,
        *,
        from_revision: int | None = None,
        fingerprint: dict[str, Any] | None = None,
    ) -> PageTemplateItem:
        record_version(self.session, template, value, actor, action, from_revision=from_revision)
        changes: dict[str, Any] = {"action": action}
        if from_revision is not None:
            changes["from_revision"] = from_revision
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page-template:" + str(template.id),
                revision=template.revision,
                changes=changes,
            )
        )
        result = await self.item(template)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            operation,
            fingerprint if fingerprint is not None else payload,
            result.model_dump(mode="json"),
            sensitive=isinstance(payload, SavePageTemplate),
        )
        await self.session.commit()
        return result

    async def publish(
        self,
        actor: User,
        template: PageTemplate,
        value: TitledPageCode,
        rows: list[ManagedPage] | None = None,
    ) -> None:
        code = value.model_dump(mode="json", exclude={"title", "description"})
        changed = template.revision > 1 and code != template.code
        template.title, template.description, template.code = value.title, value.description, code
        template.applied_revision = template.revision
        template.draft_value = None
        if changed:
            await self.update_pages(
                actor,
                template,
                rows if rows is not None else await self.affected_pages(template.id),
            )

    async def update_pages(
        self, actor: User, template: PageTemplate, rows: list[ManagedPage]
    ) -> None:
        if any(row.published for row in rows) and "pages:publish" not in user_permissions(actor):
            raise AuthFailure(403, 40399, "更新已发布页面使用的模板需要页面发布权限。")
        from app.modules.pages.service import PagesService

        service = PagesService(self.accounts, self.config)
        changes = []
        now = datetime.now(UTC)
        for row in rows:
            draft = effective_value(row.data, template)
            require_page_size(draft)
            if row.applied_snapshot is not None:
                require_page_size(effective_value(row.applied_snapshot, template))
            if row.published is None:
                continue
            row.revision += 1
            row.updated_at = now
            record_page_version(self.session, row, actor, "template_apply")
            value = effective_value(row.applied_snapshot or row.published, template)
            row.published = await service.public_value(
                row, value, None, row.published.get("share_image_url")
            )
            changes.append(CacheChange("page_changed", row.id, (row.published["path"],)))
        if changes:
            await ContentPublication(self.session, self.config).stage(*changes)
