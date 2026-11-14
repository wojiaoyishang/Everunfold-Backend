"""Create native drafts atomically and retain an idempotent import receipt."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft, DraftRevision, TranslationGroup
from app.modules.content.draft_repository import detail
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.models import Comment, Taxonomy, TaxonomyName
from app.modules.content.taxonomy_names import register_draft_terms
from app.modules.media.locking import lock_references
from app.modules.pages.models import ManagedPage, PageTemplate, PageTranslationGroup
from app.modules.pages.schemas import CUSTOM_PAGE_TYPE, TitledPageCode
from app.modules.pages.templates import effective_value, page_data
from app.modules.pages.versions import record_page_version, record_version
from app.modules.settings.models import SettingsAudit
from app.plugins.glossary.schemas import GlossaryCreate, GlossaryFields
from app.plugins.glossary.service import create_term, lock_glossary

from .planning import Plan, make_plan
from .records import NAMESPACE, CommentRecord, DraftRecord, PageRecord, TaxonomyRecord, collect
from .schemas import (
    MAX_BUNDLE_BYTES,
    MaintenanceApplyRequest,
    MaintenanceBundle,
    MaintenanceCounts,
    MaintenanceExportRequest,
    MaintenanceHistory,
    MaintenancePreview,
    MaintenanceReceipt,
)

AUDIT_RESOURCE = "maintenance:import"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def contains_null(value: object) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str) and "\x00" in current:
            return True
        if isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def bundle_hash(bundle: MaintenanceBundle) -> str:
    try:
        value = bundle.model_dump(mode="json")
        payload = canonical(value)
    except (ValueError, RecursionError) as exc:
        raise AuthFailure(422, 42281, "数据包包含无效的 JSON 数据。") from exc
    if len(payload) > MAX_BUNDLE_BYTES:
        raise AuthFailure(413, 41381, "数据包超过 16 MiB，请缩小范围。")
    if contains_null(value):
        raise AuthFailure(422, 42281, "数据包包含不能保存的控制字符。")
    return hashlib.sha256(payload).hexdigest()


def preview_value(fingerprint: str, plans: list[Plan], actor_id: UUID) -> MaintenancePreview:
    items = [entry.item for entry in plans]
    states = Counter(item.state for item in items)
    digest = hashlib.sha256(
        canonical([str(actor_id), fingerprint, [item.model_dump(mode="json") for item in items]])
    ).hexdigest()
    return MaintenancePreview(
        digest=digest,
        items=items,
        counts=MaintenanceCounts(
            create=states["create"],
            skip=states["skip"],
            conflict=states["conflict"],
            blocked=states["blocked"],
        ),
    )


def receipt(row: SettingsAudit) -> MaintenanceReceipt:
    return MaintenanceReceipt(
        id=row.id,
        created_at=row.created_at,
        counts=MaintenanceCounts.model_validate(row.changes["counts"]),
    )


class MaintenanceService:
    def __init__(self, session: AsyncSession, config: Settings, actor: Actor) -> None:
        self.session, self.config, self.actor = session, config, actor

    async def export(self, request: MaintenanceExportRequest) -> MaintenanceBundle:
        if request.date_from and request.date_to and request.date_from > request.date_to:
            raise AuthFailure(422, 42281, "开始时间必须早于结束时间。")
        # The same lock used by native content writers keeps references and their records aligned.
        await lock_references(self.session)
        bundle = MaintenanceBundle(
            format="everunfold-content",
            version=1,
            exported_at=datetime.now(UTC),
            items=await collect(self.session, request),
        )
        bundle_hash(bundle)
        return bundle

    async def preview(self, bundle: MaintenanceBundle) -> MaintenancePreview:
        fingerprint = bundle_hash(bundle)
        plans = await make_plan(self.session, self.config, bundle.items)
        return preview_value(fingerprint, plans, self.actor.id)

    async def history(self) -> MaintenanceHistory:
        rows = await self.session.scalars(
            select(SettingsAudit)
            .where(SettingsAudit.resource == AUDIT_RESOURCE)
            .order_by(SettingsAudit.created_at.desc(), SettingsAudit.id)
            .limit(50)
        )
        return MaintenanceHistory(items=[receipt(row) for row in rows])

    async def apply(self, request: MaintenanceApplyRequest) -> MaintenanceReceipt:
        fingerprint = bundle_hash(request.bundle)
        await lock_references(self.session)
        if any(item.kind == "glossary" for item in request.bundle.items):
            await lock_glossary(self.session)
        replay = await self.session.scalar(
            select(SettingsAudit).where(
                SettingsAudit.resource == AUDIT_RESOURCE,
                SettingsAudit.user_id == self.actor.id,
                SettingsAudit.changes["digest"].astext == request.digest,
                SettingsAudit.changes["bundle"].astext == fingerprint,
            )
        )
        if replay is not None:
            return receipt(replay)
        plans = await make_plan(self.session, self.config, request.bundle.items)
        preview = preview_value(fingerprint, plans, self.actor.id)
        if preview.digest != request.digest:
            raise AuthFailure(409, 40986, "预检已变化，请重新检查后再导入。")
        if not preview.counts.create:
            raise AuthFailure(422, 42282, "数据包没有可导入的记录。")
        now = datetime.now(UTC)
        try:
            await self._write(plans, now)
            row = SettingsAudit(
                id=uuid5(NAMESPACE, request.digest),
                user_id=self.actor.id,
                resource=AUDIT_RESOURCE,
                revision=1,
                created_at=now,
                changes={
                    "digest": request.digest,
                    "bundle": fingerprint,
                    "counts": preview.counts.model_dump(),
                },
            )
            self.session.add(row)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AuthFailure(409, 40986, "站内记录已变化，请重新预检。") from exc
        return receipt(row)

    async def _write(self, plans: list[Plan], now: datetime) -> None:
        pending = {plan.key: plan for plan in plans if plan.item.state == "create"}
        # Create explicit taxonomy records before draft settings register implicit terms.
        for plan in pending.values():
            if isinstance(plan.value, DraftRecord):
                settings = plan.value.fields.settings or EditorSettings()
                names = {
                    "category": {name.casefold() for name in settings.categories},
                    "tag": {name.casefold() for name in settings.tags},
                }
                plan.dependencies.update(
                    other_key
                    for other_key, other in pending.items()
                    if isinstance(other.value, TaxonomyRecord)
                    and other.value.name.casefold() in names[other_key[0]]
                )
        while pending:
            ready = [
                key for key, entry in pending.items() if entry.dependencies.isdisjoint(pending)
            ]
            if not ready:
                raise AuthFailure(409, 40986, "关联记录已变化，请重新预检。")
            for key in ready:
                await self._write_record(pending.pop(key), now)
                await self.session.flush()

    async def _write_record(self, plan: Plan, now: datetime) -> None:
        identity, kind, value = plan.item.id, plan.item.kind, plan.value
        if isinstance(value, DraftRecord):
            await self._write_draft(identity, kind, value, now)
        elif isinstance(value, PageRecord):
            await self._write_page(identity, value, now)
        elif isinstance(value, GlossaryFields):
            await create_term(
                self.session,
                self.actor,
                GlossaryCreate(
                    operation_id=uuid5(NAMESPACE, "glossary:" + str(identity)),
                    term_id=identity,
                    fields=value,
                ),
                self.config,
            )
        elif isinstance(value, TitledPageCode):
            template = PageTemplate(
                id=identity,
                title=value.title,
                description=value.description,
                revision=1,
                applied_revision=1,
                code=value.model_dump(mode="json", exclude={"title", "description"}),
            )
            self.session.add(template)
            record_version(self.session, template, value, self.actor, "import")
            self.session.add(
                SettingsAudit(
                    user_id=self.actor.id,
                    resource=f"page-template:{identity}",
                    revision=1,
                    changes={"action": "import"},
                    created_at=now,
                )
            )
        elif isinstance(value, TaxonomyRecord):
            self.session.add(
                Taxonomy(
                    id=identity,
                    kind=kind,
                    slug=value.slug,
                    name=value.name,
                    parent_id=value.parent_id,
                    retired=plan.source_status == "archived",
                    revision=1,
                )
            )
            await self.session.flush()
            self.session.add(
                TaxonomyName(
                    kind=kind,
                    name_key=value.name.casefold(),
                    taxonomy_id=identity if plan.source_status != "archived" else None,
                )
            )
        elif isinstance(value, CommentRecord):
            fields: dict[str, Any] = {
                "body_format": value.body_format,
                "website": value.website,
            }
            self.session.add(
                Comment(
                    id=identity,
                    content_id=value.target_id if value.target_kind == "content" else None,
                    page_id=value.target_id if value.target_kind == "page" else None,
                    parent_id=value.parent_id,
                    author=value.author,
                    content=value.body,
                    status="pending",
                    previous_status="pending",
                    created_at=value.created_at,
                    updated_at=now,
                    **fields,
                )
            )

    async def _write_draft(
        self,
        identity: UUID,
        kind: str,
        value: DraftRecord,
        now: datetime,
    ) -> None:
        group = await self.session.get(TranslationGroup, value.group_id)
        if group is None:
            self.session.add(
                TranslationGroup(
                    id=value.group_id, source_id=value.source_id, type_id=kind, revision=1
                )
            )
        else:
            group.revision += 1
        fields = value.fields
        settings = fields.settings or EditorSettings()
        await register_draft_terms(self.session, settings)
        row = ContentDraft(
            id=identity,
            type_id=kind,
            language=value.language,
            translation_group_id=value.group_id,
            title=fields.title,
            excerpt=fields.excerpt,
            author=self.actor.display_name,
            created_by=self.actor.id,
            body=[part.model_dump(mode="json") for part in fields.body],
            edit_mode=fields.edit_mode,
            component_document=(
                fields.component_document.model_dump(mode="json")
                if fields.component_document
                else None
            ),
            editor_settings=settings.model_dump(mode="json"),
            revision=1,
            is_demo=False,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        self.session.add(
            DraftRevision(
                draft_id=identity,
                actor_id=self.actor.id,
                revision=1,
                action="create",
                snapshot=detail(row).model_dump(mode="json"),
                created_at=now,
            )
        )

    async def _write_page(self, identity: UUID, value: PageRecord, now: datetime) -> None:
        group_id, source_id = value.group_id, value.source_id
        if group_id is not None and source_id is not None:
            group = await self.session.get(PageTranslationGroup, group_id)
            if group is None:
                self.session.add(PageTranslationGroup(id=group_id, source_id=source_id, revision=1))
            else:
                group.revision += 1
        template = (
            await self.session.get(PageTemplate, value.template_id) if value.template_id else None
        )
        page = effective_value(value.value.model_dump(mode="json"), template)
        row = ManagedPage(
            id=identity,
            source="custom" if value.type_id == CUSTOM_PAGE_TYPE else "registered",
            type_id=value.type_id,
            template_id=value.template_id,
            revision=1,
            language=value.language,
            translation_group_id=group_id,
            data=page_data(page, template),
            updated_at=now,
        )
        self.session.add(row)
        record_page_version(self.session, row, self.actor, "import")
