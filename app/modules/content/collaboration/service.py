"""Each accepted update and its idempotent ACK receipt share a PostgreSQL transaction."""

import base64
import hashlib
import logging
import traceback
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from anyio import to_thread
from pycrdt import Map
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.key_service import identity_by_digest
from app.modules.auth.models import BrowserSession, User
from app.modules.auth.permissions import Actor, AuthSubject
from app.modules.auth.repository import AuthRepository
from app.modules.auth.resource_access import require_content_access
from app.modules.auth.security import new_session_token, token_digest
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.block_sources import patched_fields, stage_block_sources
from app.modules.content.collaboration.document import (
    MAX_STATE,
    MAX_UPDATE,
    decode,
    prepare_rich,
    project,
    references,
    require_integrated,
    seed,
)
from app.modules.content.collaboration.models import (
    CollaborationDocument,
    CollaborationTicket,
    CollaborationUpdate,
)
from app.modules.content.collaboration.position_history import position_history, require_append_only
from app.modules.content.collaboration.schemas import (
    ClientUpdate,
    CommitDraft,
    CommittedDraft,
    TicketState,
)
from app.modules.content.draft_models import ContentDraft, DraftReceipt
from app.modules.content.draft_schemas import DraftFields, DraftSave, DraftSchedule
from app.modules.content.draft_service import DraftService
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.merge_lock import require_unlocked
from app.modules.media.locking import lock_references


async def authorize(
    session: AsyncSession,
    session_hash: str,
    *,
    lock: bool = False,
    draft_id: UUID | None = None,
    capability: str = "content:write",
) -> Actor:
    record = await session.get(BrowserSession, session_hash, populate_existing=True)
    if record is None:
        identity = await identity_by_digest(session, session_hash, lock=lock)
        if identity is None or identity.key.audience != "everunfold":
            raise AuthFailure(401, 40101, "登录已失效，请保留本地输入后重新登录")
        actor = identity.actor()
        if not actor.has(capability):
            raise AuthFailure(403, 40364, "没有执行此操作的权限")
        if draft_id:
            await require_content_access(session, actor, capability, draft_id)
        return actor
    user = None
    if record and record.user_id:
        statement = (
            select(User).where(User.id == record.user_id).execution_options(populate_existing=True)
        )
        # SHARE blocks role/active changes but remains compatible with audit FK KEY SHARE locks.
        # UPDATE here would deadlock with a reference writer that later records the same actor.
        user = await session.scalar(statement.with_for_update(read=True) if lock else statement)
    if not record or record.expires_at <= datetime.now(UTC) or not user or not user.active:
        raise AuthFailure(401, 40101, "登录已失效，请保留本地输入后重新登录")
    if lock:
        record = await session.get(
            BrowserSession, session_hash, populate_existing=True, with_for_update={"read": True}
        )
        if not record or record.expires_at <= datetime.now(UTC):
            raise AuthFailure(401, 40101, "登录已失效，请保留本地输入后重新登录")
    actor = Actor.browser(user, session_hash)
    if not actor.has(capability):
        raise AuthFailure(403, 40301, "当前账号没有编辑权限")
    if draft_id:
        await require_content_access(session, actor, capability, draft_id)
    return actor


async def draft_row(session: AsyncSession, draft_id: UUID, *, lock: bool = False) -> ContentDraft:
    statement = select(ContentDraft).where(ContentDraft.id == draft_id)
    row = await session.scalar(statement.with_for_update() if lock else statement)
    if row is None or row.deleted_at is not None:
        raise AuthFailure(404, 40450, "草稿不存在或已移入回收站")
    return row


async def ticket(
    session: AsyncSession, draft_id: UUID, session_hash: str, *, source_first: bool = False
) -> TicketState:
    await lock_references(session)
    row = await draft_row(session, draft_id, lock=True)
    document = await session.get(CollaborationDocument, draft_id)
    now = datetime.now(UTC)
    if document is None:
        fields = DraftFields.model_validate(
            {
                "title": row.title,
                "excerpt": row.excerpt,
                "body": row.body,
                "settings": row.editor_settings,
            }
        )
        state = seed(fields, source_first=source_first).get_update()
        document = CollaborationDocument(
            draft_id=draft_id,
            epoch=uuid4(),
            committed_seq=0,
            initial_state=state,
            state=state,
            media_ids=sorted(references(fields)),
            updated_at=now,
            initial_seq=0,
            source_first=source_first,
        )
        session.add(document)
    elif source_first and not document.source_first:
        from app.modules.content.collaboration.epochs import replace_epoch

        await require_unlocked(session, draft_id)
        await replace_epoch(
            session,
            document,
            project(decode(document.state), EditorSettings.model_validate(row.editor_settings)),
        )
    elif not document.source_first:
        doc = decode(document.state)
        vector = doc.get_state()
        if prepare_rich(doc):
            await require_unlocked(session, draft_id)
            update = doc.get_update(vector)
            document.committed_seq += 1
            document.state = doc.get_update()
            document.updated_at = now
            session.add(
                CollaborationUpdate(
                    draft_id=draft_id,
                    update_id=uuid4(),
                    committed_seq=document.committed_seq,
                    digest=hashlib.sha256(update).hexdigest(),
                    payload=update,
                    actor_id=row.created_by,
                    created_at=now,
                )
            )
    allowed = await AuthRepository(session).consume_limit(
        "collab-ticket:" + session_hash, 60, 60, now
    )
    if not allowed:
        await session.commit()
        raise AuthFailure(429, 42901, "连接过于频繁，请稍后重试")
    expired = (
        select(CollaborationTicket.digest).where(CollaborationTicket.expires_at < now).limit(100)
    )
    await session.execute(
        delete(CollaborationTicket).where(CollaborationTicket.digest.in_(expired))
    )
    secret = new_session_token()
    session.add(
        CollaborationTicket(
            digest=token_digest(secret),
            session_hash=session_hash,
            draft_id=draft_id,
            expires_at=now + timedelta(seconds=45),
        )
    )
    await session.commit()
    return TicketState(
        ticket=secret,
        draft_id=draft_id,
        epoch=document.epoch,
        ws_path=f"/ws/v1/collab/{draft_id}",
        protocol="collab-2" if document.source_first else "collab-1",
    )


async def consume_ticket(session: AsyncSession, draft_id: UUID, secret: str) -> tuple[str, Actor]:
    record = await session.scalar(
        select(CollaborationTicket)
        .where(CollaborationTicket.digest == token_digest(secret))
        .with_for_update()
    )
    if not record or record.draft_id != draft_id or record.expires_at <= datetime.now(UTC):
        raise AuthFailure(401, 40102, "协同连接凭据已失效")
    actor = await authorize(session, record.session_hash, draft_id=draft_id)
    await draft_row(session, draft_id)
    session_hash = record.session_hash
    await session.delete(record)
    await session.commit()
    return session_hash, actor


def apply_candidate(
    state: bytes, update: bytes, policy: EditorSettings, source_first: bool = False
) -> tuple[bytes, DraftFields]:
    doc = decode(state)
    history = position_history(doc)
    doc.apply_update(update)
    require_integrated(update, doc)
    require_append_only(history, doc)
    if source_first and any("rich" in block for block in doc.get("blocks", type=Map).values()):
        raise ValueError("源码协同不能混入另一份富文本正文")
    fields = project(doc, policy)
    encoded = doc.get_update()
    if len(encoded) > MAX_STATE:
        raise ValueError("协同文档已达容量限制，请导出后整理")
    return encoded, fields


async def accept_update(
    session: AsyncSession, config: Settings, draft_id: UUID, session_hash: str, update: ClientUpdate
) -> int:
    actor = await authorize(session, session_hash, lock=True, draft_id=draft_id)
    try:
        payload = base64.b64decode(update.payload, validate=True)
    except ValueError as exc:
        raise AuthFailure(422, 42270, "协同更新编码无效") from exc
    if not payload or len(payload) > MAX_UPDATE:
        raise AuthFailure(413, 41370, "协同更新过大，请导出本地内容")
    digest = hashlib.sha256(payload).hexdigest()
    await lock_references(session)
    draft = await draft_row(session, draft_id, lock=True)
    document = await session.get(CollaborationDocument, draft_id, with_for_update=True)
    if not document or document.epoch != update.epoch:
        raise AuthFailure(409, 40970, "协同版本已变化，请先导出本地内容")
    receipt = await session.get(CollaborationUpdate, (draft_id, update.update_id))
    if receipt:
        if receipt.digest != digest or receipt.actor_id != actor.id:
            raise AuthFailure(409, 40971, "更新标识已被使用")
        return receipt.committed_seq
    await require_unlocked(session, draft_id)
    now = datetime.now(UTC)
    limits = AuthRepository(session)
    allowed = await limits.consume_limit(f"collab-write:{actor.id}:{draft_id}", 160, 10, now)
    global_allowed = await limits.consume_limit(f"collab-write-user:{actor.id}", 320, 10, now)
    if not allowed or not global_allowed:
        await session.commit()
        raise AuthFailure(429, 42970, "修改过于频繁，正在等待重试")
    try:
        state, fields = await to_thread.run_sync(
            apply_candidate,
            document.state,
            payload,
            EditorSettings.model_validate(draft.editor_settings),
            document.source_first,
        )
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        location = traceback.extract_tb(exc.__traceback__)[-1]
        logging.getLogger(__name__).warning(
            "Collaborative projection rejected: %s at %s:%s",
            type(exc).__name__,
            location.name,
            location.lineno,
        )
        raise AuthFailure(422, 42270, "协同更新不符合文档规则，请导出本地内容") from exc
    from app.modules.content.collaboration.source_leases import verify_rich_update

    await verify_rich_update(session, draft_id, document.state, state, update.rich_versions)
    from app.modules.content.taxonomy_names import TermDirectory, register_draft_terms

    normalized = (await TermDirectory.load(session)).normalize(fields.settings or EditorSettings())
    await register_draft_terms(session, normalized)
    if fields.settings != normalized:
        # Keep the original client digest for retries; broadcast both its update
        # and the canonical term adjustment as one durable, integrated delta.
        candidate = decode(state)
        with candidate.transaction():
            meta = candidate.get("meta", type=Map)["settings"]
            for key in ("categories", "tags"):
                meta[key] = getattr(normalized, key)
        payload = candidate.get_update(decode(document.state).get_state())
        state = candidate.get_update()
        fields = project(candidate, EditorSettings.model_validate(draft.editor_settings))
    await editor_media(
        session, config, fields.model_dump_json(), fields.settings or EditorSettings()
    )
    document.committed_seq += 1
    document.state = state
    document.media_ids = sorted(set(document.media_ids) | references(fields))
    document.updated_at = now
    if document.source_first:
        # Durable live head is distinct from explicit version checkpoints/publication.
        draft.title, draft.excerpt = fields.title, fields.excerpt
        draft.body = [block.model_dump(mode="json") for block in fields.body]
        draft.editor_settings = (fields.settings or EditorSettings()).model_dump(mode="json")
        draft.updated_at = now
    session.add(
        CollaborationUpdate(
            draft_id=draft_id,
            update_id=update.update_id,
            committed_seq=document.committed_seq,
            digest=digest,
            payload=payload,
            actor_id=actor.id,
            created_at=now,
        )
    )
    await session.commit()
    return document.committed_seq


async def commit_draft(
    session: AsyncSession, config: Settings, actor: AuthSubject, payload: CommitDraft
) -> CommittedDraft:
    await lock_references(session)
    row = await draft_row(session, payload.id, lock=True)
    await require_unlocked(session, payload.id)
    document = await session.get(CollaborationDocument, payload.id, with_for_update=True)
    if (
        not document
        or document.epoch != payload.epoch
        or payload.committed_seq > document.committed_seq
    ):
        raise AuthFailure(409, 40970, "协同更新尚未确认，请等待同步后重试")
    if payload.committed_seq == document.committed_seq:
        state = document.state
    else:
        doc = decode(document.initial_state)
        updates = await session.scalars(
            select(CollaborationUpdate)
            .where(
                CollaborationUpdate.draft_id == payload.id,
                CollaborationUpdate.committed_seq <= payload.committed_seq,
                CollaborationUpdate.committed_seq > document.initial_seq,
            )
            .order_by(CollaborationUpdate.committed_seq)
        )
        for update in updates:
            doc.apply_update(update.payload)
        state = doc.get_update()
    policy = EditorSettings.model_validate(row.editor_settings)
    fields = project(decode(state), policy)
    committed_seq = payload.committed_seq
    if payload.block_sources:
        if payload.action in {"withdraw", "cancel_schedule"}:
            raise AuthFailure(422, 42270, "撤回或取消定时发布不能提交本地正文修改")
        fields = patched_fields(fields, payload.block_sources)
        receipt = await session.get(DraftReceipt, payload.operation_id)
        if receipt is None:
            if row.revision != payload.expected_revision:
                raise AuthFailure(409, 40951, "内容已在其他地方保存，本地修改已保留")
            if await session.get(CollaborationUpdate, (payload.id, payload.operation_id)):
                raise AuthFailure(409, 40971, "操作标识已被使用")
            committed_seq = await stage_block_sources(session, document, actor, payload, policy)
        else:
            prior_update = await session.get(
                CollaborationUpdate, (payload.id, payload.operation_id)
            )
            if prior_update:
                committed_seq = prior_update.committed_seq
    assert fields.settings is not None
    fields.settings.visibility, fields.settings.opens_at = payload.visibility, payload.opens_at
    fields.access_password = payload.access_password
    mutation = DraftSave(
        id=payload.id,
        operation_id=payload.operation_id,
        expected_revision=payload.expected_revision,
        fields=fields,
    )
    if payload.action == "schedule":
        assert payload.scheduled_for is not None
        mutation = DraftSchedule(**mutation.model_dump(), scheduled_for=payload.scheduled_for)
    result = await DraftService(session, config).mutate(
        payload.action,
        mutation,
        actor,
        collaboration_cutoff=(payload.epoch, payload.committed_seq),
        note=payload.note,
        live_fields=project(decode(document.state), policy) if document.source_first else None,
    )
    return CommittedDraft(draft=result, committed_seq=committed_seq)
