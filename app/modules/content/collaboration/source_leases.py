"""Only one staged source writer; ordinary rich edits remain CRDT updates."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pycrdt import Map
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import MAX_STATE, decode, project, references
from app.modules.content.collaboration.models import (
    CollaborationDocument,
    CollaborationUpdate,
    SourceLease,
)
from app.modules.content.collaboration.rich_text import (
    projected_html,
    rich_fingerprint,
    source_document,
    write_fragment,
)
from app.modules.content.collaboration.schemas import (
    SourceAcquire,
    SourceAcquired,
    SourceApplied,
    SourceApply,
    SourceCommand,
    SourceState,
)
from app.modules.content.collaboration.service import authorize, draft_row
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.merge_lock import require_unlocked
from app.modules.media.locking import lock_references


async def source_states(session: AsyncSession, draft_id: UUID) -> list[SourceState]:
    rows = await session.scalars(select(SourceLease).where(SourceLease.draft_id == draft_id))
    now = datetime.now(UTC)
    return [
        SourceState(
            block_id=row.block_id,
            generation=row.generation,
            client_id=row.client_id if row.expires_at and row.expires_at > now else None,
            expires_at=row.expires_at if row.expires_at and row.expires_at > now else None,
        )
        for row in rows
    ]


async def acquire_source(
    session: AsyncSession, payload: SourceAcquire, session_hash: str
) -> SourceAcquired:
    await authorize(session, session_hash, lock=True, draft_id=payload.id)
    await lock_references(session)
    await draft_row(session, payload.id, lock=True, blocks_only=True)
    await require_unlocked(session, payload.id)
    document = await session.get(CollaborationDocument, payload.id, with_for_update=True)
    if document is None:
        raise AuthFailure(409, 40970, "请先连接共享草稿")
    doc = decode(document.state)
    block = doc.get("blocks", type=Map).get(str(payload.block_id))
    if (
        not isinstance(block, Map)
        or block.get("deleted")
        or block.get("type") != "html"
        or "rich" not in block
    ):
        raise AuthFailure(409, 40973, "正文块已变化，请重新读取")
    lease = await session.get(SourceLease, (payload.id, payload.block_id))
    now = datetime.now(UTC)
    if (
        lease
        and lease.expires_at
        and lease.expires_at > now
        and (lease.client_id != payload.client_id or lease.session_hash != session_hash)
    ):
        raise AuthFailure(409, 40974, "另一位编辑者正在修改此块的 HTML 源码")
    if lease is None:
        lease = SourceLease(draft_id=payload.id, block_id=payload.block_id, generation=0)
        session.add(lease)
    lease.lease_id = uuid4()
    lease.session_hash, lease.client_id = session_hash, payload.client_id
    lease.expires_at = now + timedelta(seconds=60)
    result = SourceAcquired(
        lease_id=lease.lease_id,
        html=projected_html(block),
        expected_hash=hashlib.sha256(rich_fingerprint(block).encode()).hexdigest(),
        expires_at=lease.expires_at,
    )
    await session.commit()
    return result


async def command_source(
    session: AsyncSession, payload: SourceCommand, session_hash: str, *, release: bool
) -> SourceAcquired | None:
    await authorize(session, session_hash, lock=True, draft_id=payload.id)
    await lock_references(session)
    await draft_row(session, payload.id, lock=True, blocks_only=True)
    await require_unlocked(session, payload.id)
    lease = await session.get(SourceLease, (payload.id, payload.block_id), with_for_update=True)
    now = datetime.now(UTC)
    if not lease or lease.lease_id != payload.lease_id or lease.session_hash != session_hash:
        raise AuthFailure(409, 40974, "源码编辑权已变化，本地输入仍保留")
    if release:
        lease.expires_at = now
        await session.commit()
        return None
    if not lease.expires_at or lease.expires_at <= now:
        raise AuthFailure(409, 40974, "源码编辑权已过期，请重新获取后合并本地输入")
    lease.expires_at = now + timedelta(seconds=60)
    document = await session.get(CollaborationDocument, payload.id)
    assert document is not None
    block = decode(document.state).get("blocks", type=Map)[str(payload.block_id)]
    result = SourceAcquired(
        lease_id=payload.lease_id,
        html=projected_html(block),
        expected_hash=hashlib.sha256(rich_fingerprint(block).encode()).hexdigest(),
        expires_at=lease.expires_at,
    )
    await session.commit()
    return result


async def apply_source(
    session: AsyncSession, config: Settings, payload: SourceApply, session_hash: str
) -> SourceApplied:
    actor = await authorize(session, session_hash, lock=True, draft_id=payload.id)
    signature = hashlib.sha256(
        json.dumps(
            [str(payload.block_id), str(payload.lease_id), payload.expected_hash, payload.html],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    await lock_references(session)
    draft = await draft_row(session, payload.id, lock=True, blocks_only=True)
    await require_unlocked(session, payload.id)
    document = await session.get(CollaborationDocument, payload.id, with_for_update=True)
    lease = await session.get(SourceLease, (payload.id, payload.block_id), with_for_update=True)
    receipt = await session.get(CollaborationUpdate, (payload.id, payload.operation_id))
    if receipt:
        if receipt.source_signature != signature or receipt.actor_id != actor.id:
            raise AuthFailure(409, 40971, "操作标识已被使用")
        return SourceApplied(
            operation_id=payload.operation_id,
            committed_seq=receipt.committed_seq,
            payload=base64.b64encode(receipt.payload).decode(),
            generation=lease.generation if lease else 0,
        )
    now = datetime.now(UTC)
    if (
        not document
        or not lease
        or lease.lease_id != payload.lease_id
        or lease.session_hash != session_hash
        or not lease.expires_at
        or lease.expires_at <= now
    ):
        raise AuthFailure(409, 40974, "源码编辑权已过期，本地输入仍保留")
    doc = decode(document.state)
    block = doc.get("blocks", type=Map).get(str(payload.block_id))
    if (
        not isinstance(block, Map)
        or block.get("deleted")
        or block.get("type") != "html"
        or hashlib.sha256(rich_fingerprint(block).encode()).hexdigest() != payload.expected_hash
    ):
        raise AuthFailure(409, 40975, "正文已变化，请保留并合并本地源码")
    vector = doc.get_state()
    write_fragment(block["rich"], source_document(payload.html))
    fields = project(doc, EditorSettings.model_validate(draft.editor_settings))
    await editor_media(
        session, config, fields.model_dump_json(), fields.settings or EditorSettings()
    )
    state, update = doc.get_update(), doc.get_update(vector)
    if len(state) > MAX_STATE:
        raise AuthFailure(413, 41370, "协同文档已达容量限制，请导出后整理")
    document.committed_seq += 1
    document.state, document.updated_at = state, now
    document.media_ids = sorted(set(document.media_ids) | references(fields))
    lease.generation += 1
    lease.expires_at = now
    session.add(
        CollaborationUpdate(
            draft_id=payload.id,
            update_id=payload.operation_id,
            committed_seq=document.committed_seq,
            digest=hashlib.sha256(update).hexdigest(),
            payload=update,
            actor_id=actor.id,
            created_at=now,
            source_signature=signature,
        )
    )
    result = SourceApplied(
        operation_id=payload.operation_id,
        committed_seq=document.committed_seq,
        payload=base64.b64encode(update).decode(),
        generation=lease.generation,
    )
    await session.commit()
    return result


async def verify_rich_update(
    session: AsyncSession, draft_id: UUID, before: bytes, after: bytes, versions: dict[UUID, int]
) -> None:
    prior, candidate = decode(before).get("blocks", type=Map), decode(after).get("blocks", type=Map)
    rows = {
        str(row.block_id): row
        for row in await session.scalars(
            select(SourceLease).where(SourceLease.draft_id == draft_id)
        )
    }
    now = datetime.now(UTC)
    for key, version in versions.items():
        row = rows.get(str(key))
        if version != (row.generation if row else 0):
            raise AuthFailure(409, 40975, "HTML 源码已更新，请导出并合并本地输入后重新连接")
    for key, old in prior.items():
        if not isinstance(old, Map) or "rich" not in old or key not in candidate:
            continue
        new = candidate[key]
        if not isinstance(new, Map):
            continue
        changed = (
            rich_fingerprint(old) != rich_fingerprint(new)
            or old.get("deleted") != new.get("deleted")
            or old.get("type") != new.get("type")
        )
        row = rows.get(key)
        if changed and row and row.expires_at and row.expires_at > now:
            raise AuthFailure(409, 40974, "此块正在编辑 HTML 源码，本地输入已保留，稍后重试")
        if new.get("type") == "html" and (
            str(new.get("content")) != str(old.get("content"))
            or new.get("rich_base") != old.get("rich_base")
        ):
            raise AuthFailure(422, 42270, "HTML 原文不能覆盖共享富文本")
        if changed and versions.get(UUID(key), 0) != (row.generation if row else 0):
            raise AuthFailure(409, 40975, "HTML 源码已更新，请保留本地输入后重新连接")
