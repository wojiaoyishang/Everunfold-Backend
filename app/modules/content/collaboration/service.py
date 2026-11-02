"""Each accepted update and its idempotent ACK receipt share a PostgreSQL transaction."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from anyio import to_thread
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.models import BrowserSession, User
from app.modules.auth.repository import AuthRepository
from app.modules.auth.security import new_session_token, token_digest
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import (
    MAX_STATE,
    MAX_UPDATE,
    decode,
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
from app.modules.content.collaboration.schemas import (
    ClientUpdate,
    CommitDraft,
    CommittedDraft,
    TicketState,
)
from app.modules.content.draft_models import ContentDraft
from app.modules.content.draft_schemas import DraftFields, DraftSave
from app.modules.content.draft_service import DraftService
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.media.locking import lock_references


async def authorize(session: AsyncSession, session_hash: str) -> User:
    record = await session.get(BrowserSession, session_hash, populate_existing=True)
    user = (
        await session.get(User, record.user_id, populate_existing=True)
        if record and record.user_id
        else None
    )
    if not record or record.expires_at <= datetime.now(UTC) or not user or not user.active:
        raise AuthFailure(401, 40101, "登录已失效，请保留本地输入后重新登录")
    if user.role != "admin":
        raise AuthFailure(403, 40301, "当前账号没有编辑权限")
    return user


async def draft_row(session: AsyncSession, draft_id: UUID, *, lock: bool = False) -> ContentDraft:
    statement = select(ContentDraft).where(ContentDraft.id == draft_id)
    row = await session.scalar(statement.with_for_update() if lock else statement)
    if row is None or row.deleted_at is not None:
        raise AuthFailure(404, 40450, "草稿不存在或已移入回收站")
    return row


async def ticket(session: AsyncSession, draft_id: UUID, session_hash: str) -> TicketState:
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
        state = seed(fields).get_update()
        document = CollaborationDocument(
            draft_id=draft_id,
            epoch=uuid4(),
            committed_seq=0,
            initial_state=state,
            state=state,
            media_ids=sorted(references(fields)),
            updated_at=now,
        )
        session.add(document)
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
        ticket=secret, draft_id=draft_id, epoch=document.epoch, ws_path=f"/ws/v1/collab/{draft_id}"
    )


async def consume_ticket(session: AsyncSession, draft_id: UUID, secret: str) -> tuple[str, User]:
    record = await session.scalar(
        select(CollaborationTicket)
        .where(CollaborationTicket.digest == token_digest(secret))
        .with_for_update()
    )
    if not record or record.draft_id != draft_id or record.expires_at <= datetime.now(UTC):
        raise AuthFailure(401, 40102, "协同连接凭据已失效")
    actor = await authorize(session, record.session_hash)
    await draft_row(session, draft_id)
    session_hash = record.session_hash
    await session.delete(record)
    await session.commit()
    return session_hash, actor


def apply_candidate(
    state: bytes, update: bytes, policy: EditorSettings
) -> tuple[bytes, DraftFields]:
    doc = decode(state)
    doc.apply_update(update)
    require_integrated(update, doc)
    fields = project(doc, policy)
    encoded = doc.get_update()
    if len(encoded) > MAX_STATE:
        raise ValueError("协同文档已达容量限制，请导出后整理")
    return encoded, fields


async def accept_update(
    session: AsyncSession, config: Settings, draft_id: UUID, session_hash: str, update: ClientUpdate
) -> int:
    actor = await authorize(session, session_hash)
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
    now = datetime.now(UTC)
    if not await AuthRepository(session).consume_limit(
        f"collab-write:{actor.id}:{draft_id}", 160, 10, now
    ):
        await session.commit()
        raise AuthFailure(429, 42970, "修改过于频繁，正在等待重试")
    try:
        state, fields = await to_thread.run_sync(
            apply_candidate,
            document.state,
            payload,
            EditorSettings.model_validate(draft.editor_settings),
        )
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise AuthFailure(422, 42270, "协同更新不符合文档规则，请导出本地内容") from exc
    await editor_media(
        session, config, fields.model_dump_json(), fields.settings or EditorSettings()
    )
    document.committed_seq += 1
    document.state = state
    document.media_ids = sorted(set(document.media_ids) | references(fields))
    document.updated_at = now
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
    session: AsyncSession, config: Settings, actor: User, payload: CommitDraft
) -> CommittedDraft:
    await lock_references(session)
    row = await draft_row(session, payload.id, lock=True)
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
            )
            .order_by(CollaborationUpdate.committed_seq)
        )
        for update in updates:
            doc.apply_update(update.payload)
        state = doc.get_update()
    fields = project(decode(state), EditorSettings.model_validate(row.editor_settings))
    assert fields.settings is not None
    fields.settings.visibility, fields.settings.opens_at = payload.visibility, payload.opens_at
    fields.access_password = payload.access_password
    mutation = DraftSave(
        id=payload.id,
        operation_id=payload.operation_id,
        expected_revision=payload.expected_revision,
        fields=fields,
    )
    result = await DraftService(session, config).mutate(
        payload.action, mutation, actor, collaborative=True
    )
    return CommittedDraft(draft=result, committed_seq=payload.committed_seq)
