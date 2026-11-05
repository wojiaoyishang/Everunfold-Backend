"""Retained epoch seeds permit explicit offline recovery after a merge resets the CRDT."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import decode, seed
from app.modules.content.collaboration.models import (
    CollaborationDocument,
    CollaborationEpoch,
    CollaborationUpdate,
)
from app.modules.content.draft_schemas import DraftFields


async def state_at(session: AsyncSession, draft_id: UUID, epoch: UUID, seq: int) -> bytes:
    head = await session.get(CollaborationDocument, draft_id)
    if head and head.epoch == epoch:
        initial, start, end = head.initial_state, head.initial_seq, head.committed_seq
        if seq == end:
            return head.state
    else:
        history = await session.get(CollaborationEpoch, (draft_id, epoch))
        if history is None or history.final_seq is None:
            raise AuthFailure(409, 40981, "找不到草稿的共同基线，请保留本地内容")
        initial, start, end = history.initial_state, history.initial_seq, history.final_seq
    if not start <= seq <= end:
        raise AuthFailure(409, 40981, "草稿基线截点无效")
    doc = decode(initial)
    updates = await session.scalars(
        select(CollaborationUpdate)
        .where(
            CollaborationUpdate.draft_id == draft_id,
            CollaborationUpdate.committed_seq > start,
            CollaborationUpdate.committed_seq <= seq,
        )
        .order_by(CollaborationUpdate.committed_seq)
    )
    for update in updates:
        doc.apply_update(update.payload)
    return doc.get_update()


async def replace_epoch(
    session: AsyncSession, head: CollaborationDocument, fields: DraftFields
) -> None:
    prior = await session.get(CollaborationEpoch, (head.draft_id, head.epoch))
    if prior is None:
        prior = CollaborationEpoch(
            draft_id=head.draft_id,
            epoch=head.epoch,
            initial_seq=head.initial_seq,
            initial_state=head.initial_state,
        )
        session.add(prior)
    prior.final_seq = head.committed_seq
    state = seed(fields, source_first=True).get_update()
    head.epoch = uuid4()
    head.committed_seq += 1
    head.initial_seq = head.committed_seq
    head.initial_state = head.state = state
    head.source_first = True
    session.add(
        CollaborationEpoch(
            draft_id=head.draft_id,
            epoch=head.epoch,
            initial_seq=head.initial_seq,
            initial_state=state,
        )
    )
