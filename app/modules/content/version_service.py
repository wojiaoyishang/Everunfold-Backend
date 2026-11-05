"""Version browsing, detached branch receipts, and atomic merge state transitions."""

import base64
import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID

from anyio import to_thread
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import decode, project, references
from app.modules.content.collaboration.epochs import replace_epoch, state_at
from app.modules.content.collaboration.models import CollaborationDocument, CollaborationUpdate
from app.modules.content.collaboration.service import draft_row
from app.modules.content.draft_models import ContentDraft, DraftBranch, DraftMerge, DraftRevision
from app.modules.content.draft_schemas import DraftFields
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.merge_algorithm import resolve_value, three_way
from app.modules.content.merge_lock import lock_state, require_unlocked
from app.modules.content.version_schemas import (
    BranchDetail,
    BranchItem,
    BranchLookup,
    BranchUpload,
    MergeBegin,
    MergeConflict,
    MergeDetail,
    MergeLookup,
    MergeResolve,
    MergeSummary,
    VersionCatalog,
    VersionDetail,
    VersionItem,
    VersionLookup,
    VersionQuery,
)
from app.modules.media.locking import lock_references

LEASE_SECONDS = 60


def safe_fields(fields: DraftFields) -> DraftFields:
    fields = fields.model_copy(deep=True)
    fields.access_password = None
    if fields.settings:
        fields.settings.visibility, fields.settings.opens_at = "public", None
    return fields


def snapshot_fields(row: ContentDraft) -> DraftFields:
    return DraftFields.model_validate(
        {
            "title": row.title,
            "excerpt": row.excerpt,
            "body": row.body,
            "settings": row.editor_settings,
        }
    )


def historical_fields(item: DraftRevision) -> DraftFields:
    return DraftFields.model_validate(
        {key: item.snapshot[key] for key in ("title", "excerpt", "body", "settings")}
    )


async def version_item(session: AsyncSession, item: DraftRevision, latest: int) -> VersionItem:
    actor = await session.get(User, item.actor_id)
    return VersionItem(
        revision=item.revision,
        actor=actor.display_name if actor else "编辑者",
        action=item.action,
        note=item.note,
        created_at=item.created_at,
        epoch=item.epoch,
        committed_seq=item.committed_seq,
        parents=item.parents,
        current=item.revision == latest,
    )


async def branch_item(session: AsyncSession, item: DraftBranch) -> BranchItem:
    actor = await session.get(User, item.actor_id)
    return BranchItem(
        id=item.id,
        actor=actor.display_name if actor else "编辑者",
        note=item.note,
        base_epoch=item.base_epoch,
        base_seq=item.base_seq,
        base_revision=item.base_revision,
        created_at=item.created_at,
        merged_revision=item.merged_revision,
        review_blocks=item.base.get("_review_blocks", []),
    )


async def catalog(session: AsyncSession, payload: VersionQuery) -> VersionCatalog:
    row = await draft_row(session, payload.id)
    versions = select(DraftRevision).where(
        DraftRevision.draft_id == row.id, DraftRevision.deleted_at.is_(None)
    )
    branches = select(DraftBranch).where(
        DraftBranch.draft_id == row.id, DraftBranch.deleted_at.is_(None)
    )
    merges = select(DraftMerge).where(
        DraftMerge.draft_id == row.id, DraftMerge.status != "discarded"
    )
    total = await session.scalar(select(func.count()).select_from(versions.subquery())) or 0
    branch_total = await session.scalar(select(func.count()).select_from(branches.subquery())) or 0
    return VersionCatalog(
        versions=[
            await version_item(session, item, row.revision)
            for item in await session.scalars(
                versions.order_by(DraftRevision.revision.desc())
                .offset((payload.page - 1) * 20)
                .limit(20)
            )
        ],
        branches=[
            await branch_item(session, item)
            for item in await session.scalars(
                branches.order_by(DraftBranch.created_at.desc(), DraftBranch.id)
                .offset((payload.branch_page - 1) * 20)
                .limit(20)
            )
        ],
        total=total,
        branch_total=branch_total,
        page=payload.page,
        branch_page=payload.branch_page,
        lock=await lock_state(session, row.id),
        merges=[
            await merge_summary(session, item)
            for item in await session.scalars(
                merges.order_by(DraftMerge.created_at.desc())
                .offset((payload.merge_page - 1) * 20)
                .limit(20)
            )
        ],
        merge_total=await session.scalar(select(func.count()).select_from(merges.subquery())) or 0,
        merge_page=payload.merge_page,
    )


async def read_version(
    session: AsyncSession, payload: VersionLookup, *, remove: bool = False
) -> VersionDetail:
    row = await draft_row(session, payload.id, lock=remove)
    item = await session.get(DraftRevision, (payload.id, payload.revision))
    if item is None or item.deleted_at:
        raise AuthFailure(404, 40451, "历史草稿不存在或已删除")
    result = VersionDetail(
        version=await version_item(session, item, row.revision),
        fields=safe_fields(historical_fields(item)),
    )
    if remove:
        if item.revision == row.revision:
            raise AuthFailure(409, 40982, "当前最新版不能删除")
        item.deleted_at = datetime.now(UTC)
        await session.commit()
    return result


async def upload_branch(
    session: AsyncSession, config: Settings, actor: User, payload: BranchUpload
) -> BranchDetail:
    await lock_references(session)
    row = await draft_row(session, payload.id, lock=True)
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    branch = await session.get(DraftBranch, payload.branch_id)
    if branch:
        if (
            branch.draft_id != row.id
            or branch.actor_id != actor.id
            or branch.digest != digest
            or branch.deleted_at
        ):
            raise AuthFailure(409, 40983, "草稿分支编号已用于另一项修改")
    else:
        review_blocks = [str(key) for key in payload.review_blocks]
        if payload.fields.access_password:
            raise AuthFailure(422, 42281, "文章密码不能保存在分支草稿中")
        if payload.base_branch_id is not None:
            parent = await session.get(DraftBranch, payload.base_branch_id)
            if parent is None or parent.draft_id != row.id:
                raise AuthFailure(404, 40452, "找不到草稿的共同基线")
            base = branch_base(parent)
            review_blocks = list(set(review_blocks) | set(parent.base.get("_review_blocks", [])))
        elif payload.base_epoch is not None and payload.base_seq is not None:
            doc = decode(await state_at(session, row.id, payload.base_epoch, payload.base_seq))
            # Reconcile accepted operations whose ACK was lost, without importing other
            # users' intervening changes into the local side or counting our edit twice.
            receipts = []
            seen: set[UUID] = set()
            for update in payload.updates:
                if update.update_id in seen:
                    raise AuthFailure(422, 42281, "草稿更新编号重复")
                seen.add(update.update_id)
                receipt = await session.get(CollaborationUpdate, (row.id, update.update_id))
                if receipt:
                    try:
                        raw = base64.b64decode(update.payload, validate=True)
                    except ValueError as exc:
                        raise AuthFailure(422, 42281, "草稿更新编码无效") from exc
                    if (
                        receipt.actor_id != actor.id
                        or receipt.digest != hashlib.sha256(raw).hexdigest()
                    ):
                        raise AuthFailure(409, 40983, "草稿更新回执不匹配")
                    if receipt.committed_seq > payload.base_seq:
                        receipts.append(receipt)
            for receipt in sorted(receipts, key=lambda item: item.committed_seq):
                doc.apply_update(receipt.payload)
            base = project(doc, EditorSettings.model_validate(row.editor_settings))
        elif payload.base_revision is not None:
            old = await session.get(DraftRevision, (row.id, payload.base_revision))
            if old is None:
                raise AuthFailure(404, 40451, "历史草稿不存在或已删除")
            base = historical_fields(old)
        else:
            raise AuthFailure(422, 42281, "请提供草稿的共同基线")
        fields = safe_fields(payload.fields)
        await editor_media(
            session, config, fields.model_dump_json(), fields.settings or EditorSettings()
        )
        base_snapshot = safe_fields(base).model_dump(mode="json")
        if review_blocks:
            base_snapshot["_review_blocks"] = review_blocks
        branch = DraftBranch(
            id=payload.branch_id,
            draft_id=row.id,
            actor_id=actor.id,
            note=payload.note,
            base_epoch=payload.base_epoch,
            base_seq=payload.base_seq,
            base_revision=payload.base_revision,
            base=base_snapshot,
            fields=fields.model_dump(mode="json"),
            digest=digest,
            created_at=datetime.now(UTC),
        )
        session.add(branch)
        # Existing media deletion guard scans immutable history as well as live docs.
        head = await session.get(CollaborationDocument, row.id)
        if head:
            head.media_ids = sorted(set(head.media_ids) | references(base) | references(fields))
        await session.commit()
    return BranchDetail(
        branch=await branch_item(session, branch),
        base=branch_base(branch),
        fields=DraftFields.model_validate(branch.fields),
    )


async def read_branch(
    session: AsyncSession, payload: BranchLookup, *, remove: bool = False
) -> BranchDetail:
    await draft_row(session, payload.id, lock=remove)
    branch = await session.get(DraftBranch, payload.branch_id)
    if branch is None or branch.draft_id != payload.id or branch.deleted_at:
        raise AuthFailure(404, 40452, "分支草稿不存在或已删除")
    result = BranchDetail(
        branch=await branch_item(session, branch),
        base=branch_base(branch),
        fields=DraftFields.model_validate(branch.fields),
    )
    if remove:
        active = await lock_state(session, payload.id)
        if active:
            merge = await session.get(DraftMerge, active.id)
            if merge and merge.branch_id == branch.id:
                raise AuthFailure(409, 40980, "此草稿正在合并，请先取消合并")
        branch.deleted_at = datetime.now(UTC)
        await session.commit()
    return result


def branch_base(branch: DraftBranch) -> DraftFields:
    return DraftFields.model_validate(
        {key: value for key, value in branch.base.items() if key != "_review_blocks"}
    )


def merge_detail(item: DraftMerge) -> MergeDetail:
    status = item.status
    if status == "active" and item.expires_at <= datetime.now(UTC):
        status = "expired"
    return MergeDetail.model_validate(
        {
            **{
                key: getattr(item, key)
                for key in (
                    "id",
                    "branch_id",
                    "epoch",
                    "target_seq",
                    "target_revision",
                    "expires_at",
                    "base",
                    "local",
                    "remote",
                    "result",
                    "conflicts",
                    "resolved",
                    "result_revision",
                )
            },
            "owner_id": item.actor_id,
            "status": status,
        }
    )


async def merge_summary(session: AsyncSession, item: DraftMerge) -> MergeSummary:
    actor = await session.get(User, item.actor_id)
    return MergeSummary(
        id=item.id,
        branch_id=item.branch_id,
        owner_id=item.actor_id,
        actor=actor.display_name if actor else "编辑者",
        status=merge_detail(item).status,
        created_at=item.created_at,
        expires_at=item.expires_at,
        unresolved=len(set(c["key"] for c in item.conflicts) - set(item.resolved)),
        result_revision=item.result_revision,
    )


async def auto_checkpoint(session: AsyncSession, draft_id: UUID) -> None:
    now = datetime.now(UTC)
    head = await session.get(CollaborationDocument, draft_id)
    if not head or not head.source_first or (now - head.updated_at).total_seconds() < 10:
        return
    await lock_references(session)
    row = await draft_row(session, draft_id, lock=True)
    head = await session.get(
        CollaborationDocument, draft_id, populate_existing=True, with_for_update=True
    )
    assert head is not None
    prior = await session.get(DraftRevision, (draft_id, row.revision))
    if (
        (now - head.updated_at).total_seconds() < 10
        or (
            prior
            and (
                prior.epoch == head.epoch
                and prior.committed_seq == head.committed_seq
                or (now - prior.created_at).total_seconds() < 60
            )
        )
        or await lock_state(session, draft_id)
    ):
        return
    from app.modules.content.draft_repository import detail

    row.revision += 1
    actor = await session.scalar(
        select(CollaborationUpdate.actor_id)
        .where(CollaborationUpdate.draft_id == draft_id)
        .order_by(CollaborationUpdate.committed_seq.desc())
        .limit(1)
    )
    session.add(
        DraftRevision(
            draft_id=draft_id,
            revision=row.revision,
            actor_id=actor or row.created_by,
            action="autosave",
            note="自动检查点",
            snapshot=detail(row).model_dump(mode="json"),
            epoch=head.epoch,
            committed_seq=head.committed_seq,
            parents=[row.revision - 1],
            created_at=now,
        )
    )
    await session.commit()


async def begin_merge(session: AsyncSession, actor: User, payload: MergeBegin) -> MergeDetail:
    await lock_references(session)
    row = await draft_row(session, payload.id, lock=True)
    previous = await session.get(DraftMerge, payload.merge_id)
    if previous:
        if (
            previous.draft_id != row.id
            or previous.branch_id != payload.branch_id
            or previous.actor_id != actor.id
        ):
            raise AuthFailure(409, 40983, "合并编号已用于另一项操作")
        return merge_detail(previous)
    await require_unlocked(session, row.id)
    branch = await read_branch(session, payload)
    head = await session.get(CollaborationDocument, row.id, with_for_update=True)
    if head is None:
        raise AuthFailure(409, 40970, "请先连接这篇文章的协同服务")
    remote = safe_fields(
        project(decode(head.state), EditorSettings.model_validate(row.editor_settings))
    )
    result, conflicts = await to_thread.run_sync(
        three_way,
        branch.base.model_dump(mode="json"),
        branch.fields.model_dump(mode="json"),
        remote.model_dump(mode="json"),
    )
    for block_id in branch.branch.review_blocks:
        key = str(block_id)
        if any(
            conflict.path == ["body"] or conflict.path[:2] == ["body", key]
            for conflict in conflicts
        ):
            continue
        versions = [
            {str(block.id): block.model_dump(mode="json") for block in fields.body}
            for fields in (branch.base, branch.fields, remote)
        ]
        conflicts.append(
            MergeConflict(
                key="body/" + key,
                path=["body", key],
                kind="block",
                base=versions[0].get(key),
                local=versions[1].get(key),
                remote=versions[2].get(key),
            )
        )
    DraftFields.model_validate(result)
    now = datetime.now(UTC)
    item = DraftMerge(
        id=payload.merge_id,
        draft_id=row.id,
        branch_id=branch.branch.id,
        actor_id=actor.id,
        epoch=head.epoch,
        target_seq=head.committed_seq,
        target_revision=row.revision,
        status="active",
        base=branch.base.model_dump(mode="json"),
        local=branch.fields.model_dump(mode="json"),
        remote=remote.model_dump(mode="json"),
        result=result,
        conflicts=[conflict.model_dump(mode="json") for conflict in conflicts],
        resolved=[],
        expires_at=now + timedelta(seconds=LEASE_SECONDS),
        created_at=now,
    )
    session.add(item)
    await session.commit()
    return merge_detail(item)


async def owned_merge(
    session: AsyncSession, actor: User, payload: MergeLookup, *, active: bool = True
) -> DraftMerge:
    await lock_references(session)
    await draft_row(session, payload.id, lock=True)
    item = await session.get(DraftMerge, payload.merge_id, with_for_update=True)
    if item is None or item.draft_id != payload.id:
        raise AuthFailure(404, 40453, "找不到这项合并")
    if item.actor_id != actor.id:
        raise AuthFailure(403, 40301, "只有合并发起人可以处理这项合并")
    if active and (item.status != "active" or item.expires_at <= datetime.now(UTC)):
        raise AuthFailure(409, 40984, "合并已结束或编辑权已过期，结果已保留，请重新比较最新版")
    return item


async def resolve_merge(session: AsyncSession, actor: User, payload: MergeResolve) -> MergeDetail:
    item = await owned_merge(session, actor, payload)
    result = (
        safe_fields(payload.result).model_dump(mode="json")
        if payload.result is not None
        else deepcopy(item.result)
    )
    conflicts = {conflict["key"]: conflict for conflict in item.conflicts}
    resolved = set(item.resolved)
    for resolution in payload.resolutions:
        conflict = conflicts.get(resolution.key)
        if conflict is None:
            raise AuthFailure(422, 42282, "找不到这项冲突")
        chosen = resolution.value if resolution.choice == "custom" else conflict[resolution.choice]
        try:
            resolve_value(result, conflict["path"], chosen)
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthFailure(422, 42282, "合并结果格式无效") from exc
        resolved.add(resolution.key)
    # Validate structure before persisting any conflict decision.
    result = safe_fields(DraftFields.model_validate(result)).model_dump(mode="json")
    item.result, item.resolved = result, sorted(resolved)
    item.expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    await session.commit()
    return merge_detail(item)


async def command_merge(
    session: AsyncSession, config: Settings, actor: User, payload: MergeLookup, command: str
) -> MergeDetail:
    item = await owned_merge(session, actor, payload, active=False)
    if (command == "finish" and item.status == "completed") or (
        command == "cancel" and item.status == "cancelled"
    ):
        return merge_detail(item)
    if command == "delete":
        if item.status == "active" and item.expires_at > datetime.now(UTC):
            raise AuthFailure(409, 40980, "请先暂停这项合并")
        row = await draft_row(session, payload.id, lock=True)
        if item.result_revision == row.revision:
            raise AuthFailure(409, 40982, "最新版关联的合并记录不能删除")
        item.status = "discarded"
        await session.commit()
        return merge_detail(item)
    if item.status != "active" or item.expires_at <= datetime.now(UTC):
        raise AuthFailure(409, 40984, "合并编辑权已过期，结果已保留，请重新比较最新版")
    if command == "renew":
        item.expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    elif command == "cancel":
        item.status = "cancelled"
    elif command == "finish":
        if set(item.resolved) != {conflict["key"] for conflict in item.conflicts}:
            raise AuthFailure(409, 40985, "请先处理全部合并冲突")
        row = await draft_row(session, payload.id, lock=True)
        head = await session.get(CollaborationDocument, row.id, with_for_update=True)
        if not head or (head.epoch, head.committed_seq, row.revision) != (
            item.epoch,
            item.target_seq,
            item.target_revision,
        ):
            raise AuthFailure(409, 40984, "合并目标已变化，请重新比较")
        fields = DraftFields.model_validate(item.result)
        settings = fields.settings or EditorSettings()
        prior = EditorSettings.model_validate(row.editor_settings)
        settings.visibility, settings.opens_at = prior.visibility, prior.opens_at
        fields.settings = settings
        await editor_media(session, config, fields.model_dump_json(), settings)
        from app.modules.content.draft_repository import detail
        from app.modules.content.draft_service import DraftService, require_type

        if require_type(row.type_id).title_required and not fields.title.strip():
            raise AuthFailure(422, 42251, "请填写标题")
        await DraftService(session, config)._check_media(fields.model_dump_json())
        row.title, row.excerpt = fields.title, fields.excerpt
        row.body = [block.model_dump(mode="json") for block in fields.body]
        row.editor_settings = settings.model_dump(mode="json")
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        await replace_epoch(session, head, fields)
        head.updated_at = row.updated_at
        head.media_ids = sorted(set(head.media_ids) | references(fields))
        branch = await session.get(DraftBranch, item.branch_id)
        assert branch is not None
        branch.merged_revision = row.revision
        item.status, item.result_revision = "completed", row.revision
        session.add(
            DraftRevision(
                draft_id=row.id,
                revision=row.revision,
                actor_id=actor.id,
                action="merge",
                snapshot=detail(row).model_dump(mode="json"),
                note=branch.note,
                epoch=head.epoch,
                committed_seq=head.committed_seq,
                parents=list(
                    dict.fromkeys(
                        [
                            item.target_revision,
                            *([branch.base_revision] if branch.base_revision else []),
                        ]
                    )
                ),
                created_at=row.updated_at,
            )
        )
    else:
        raise ValueError("Unknown merge command")
    await session.commit()
    return merge_detail(item)
