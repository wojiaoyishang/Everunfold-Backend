"""Atomic list mutations reuse live metadata and publication/version services."""

from uuid import uuid5

from anyio import CapacityLimiter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.operations import AccountOperations
from app.modules.auth.permissions import Actor
from app.modules.auth.resource_access import require_content_access
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.models import CollaborationDocument
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.draft_repository import detail
from app.modules.content.draft_schemas import DraftBatchResult, DraftMutation
from app.modules.content.draft_service import DraftService, require_type
from app.modules.content.management_schemas import DraftBatch
from app.modules.content.merge_lock import require_unlocked
from app.modules.content.metadata_edits import stage_metadata
from app.modules.content.taxonomy_names import TermDirectory
from app.modules.media.locking import lock_references
from app.modules.settings.models import SettingsAudit


async def mutate_batch(
    session: AsyncSession,
    config: Settings,
    actor: Actor,
    payload: DraftBatch,
) -> DraftBatchResult:
    await lock_references(session)
    expected = {item.id: item for item in payload.items}
    for identity in expected:
        await require_content_access(session, actor, "content:write", identity)
    operations = AccountOperations(session, CapacityLimiter(1))
    remembered = await operations.read(payload.operation_id, actor.id, "content_batch", payload)
    if remembered is not None:
        await session.commit()
        return DraftBatchResult.model_validate(remembered)
    rows = list(
        await session.scalars(
            select(ContentDraft)
            .where(ContentDraft.id.in_(expected))
            .order_by(ContentDraft.id)
            .with_for_update()
        )
    )
    if len(rows) != len(expected):
        raise AuthFailure(404, 40450, "部分内容不存在，请刷新列表")
    for row in rows:
        version = expected[row.id]
        head = await session.get(CollaborationDocument, row.id, with_for_update=True)
        if (
            row.revision != version.expected_revision
            or (head.committed_seq if head else None) != version.expected_seq
        ):
            raise AuthFailure(409, 40951, "所选内容已被修改，未保存任何项目；请刷新后重新选择")
        await require_unlocked(session, row.id)
        if bool(row.deleted_at) != (payload.action == "restore"):
            raise AuthFailure(409, 40952, "部分内容状态已变化，请刷新列表")
        if payload.action == "trash":
            if row.published_revision:
                await require_content_access(session, actor, "content:publish", row.id)
            group = await session.get(TranslationGroup, row.translation_group_id)
            if group and group.source_id == row.id:
                members = await session.scalars(
                    select(ContentDraft.id).where(
                        ContentDraft.translation_group_id == group.id,
                        ContentDraft.deleted_at.is_(None),
                    )
                )
                if any(member not in expected for member in members):
                    raise AuthFailure(409, 40995, "回收原文需要明确包含该组全部语言版本")
        if payload.patch:
            definition = require_type(row.type_id)
            if (
                payload.patch.title is not None
                and definition.title_required
                and not payload.patch.title.strip()
            ):
                raise AuthFailure(422, 42251, "请填写标题")
            if payload.patch.excerpt and not definition.supports_excerpt:
                raise AuthFailure(422, 42252, "此内容类型不使用独立摘要")
    directory = await TermDirectory.load(session)
    results = []
    for row in rows:
        if payload.action == "edit":
            assert payload.patch is not None
            await stage_metadata(
                session,
                row,
                actor.id,
                payload.patch,
                directory,
                note="列表快速编辑" if len(rows) == 1 else "列表批量编辑",
            )
            result = detail(row)
        else:
            result = await DraftService(session, config).mutate(
                payload.action,
                DraftMutation(
                    id=row.id,
                    expected_revision=row.revision,
                    operation_id=uuid5(payload.operation_id, str(row.id)),
                ),
                actor,
                commit=False,
            )
        head = await session.get(CollaborationDocument, row.id)
        result.collaboration_seq = head.committed_seq if head else None
        results.append(result)
    batch_result = DraftBatchResult(items=results)
    session.add(
        SettingsAudit(
            user_id=actor.id,
            resource="content-batch",
            revision=1,
            changes={
                "action": payload.action,
                "ids": [str(row.id) for row in rows],
                "fields": list(payload.patch.model_dump(exclude_none=True))
                if payload.patch
                else [],
            },
        )
    )
    await operations.remember(
        payload.operation_id,
        actor.id,
        "content_batch",
        payload,
        batch_result.model_dump(mode="json"),
    )
    await session.commit()
    return batch_result
