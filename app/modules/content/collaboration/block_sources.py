"""Stage canonical source and its CRDT receipt in the formal draft transaction."""

import hashlib
from datetime import UTC, datetime
from typing import Any

from pycrdt import Map, Text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import AuthSubject
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import MAX_STATE, decode, project, references
from app.modules.content.collaboration.models import (
    CollaborationDocument,
    CollaborationUpdate,
    SourceLease,
)
from app.modules.content.collaboration.rich_text import (
    projected_html,
    render_node,
    source_document,
    write_fragment,
    xml_document,
)
from app.modules.content.collaboration.schemas import BlockSourcePatch, CommitDraft
from app.modules.content.draft_schemas import DraftFields
from app.modules.content.editor_schema import EditorSettings


def patched_fields(fields: DraftFields, patches: list[BlockSourcePatch]) -> DraftFields:
    by_id = {patch.id: patch for patch in patches}
    if len(by_id) != len(patches):
        raise AuthFailure(422, 42270, "正文块修改重复")
    body = []
    for block in fields.body:
        patch = by_id.pop(block.id, None)
        if patch:
            if (
                block.type != patch.type
                or hashlib.sha256(block.content.encode()).hexdigest() != patch.base_hash
            ):
                raise AuthFailure(409, 40976, "同一个正文块已更新，本地修改已保留")
            block = block.model_copy(update={"content": patch.content})
        body.append(block)
    if by_id:
        raise AuthFailure(409, 40976, "正文块已移除，本地修改已保留")
    if sum(len(block.content) for block in body) > 200_000:
        raise AuthFailure(422, 42270, "正文不能超过 200000 字符")
    return fields.model_copy(update={"body": body})


async def stage_block_sources(
    session: AsyncSession,
    document: CollaborationDocument,
    actor: AuthSubject,
    payload: CommitDraft,
    policy: EditorSettings,
) -> int:
    doc = decode(document.state)
    # Verify against the head as well as the historical save cutoff. Another API
    # process or a legacy client may have updated a touched block in between.
    patched_fields(project(doc, policy), payload.block_sources)
    blocks = doc.get("blocks", type=Map)
    vector = doc.get_state()
    now = datetime.now(UTC)
    changed = False
    for patch in payload.block_sources:
        block = blocks[str(patch.id)]
        if patch.content == projected_html(block):
            continue
        changed = True
        if patch.type == "html":
            lease = await session.get(SourceLease, (payload.id, patch.id))
            if lease and lease.expires_at and lease.expires_at > now:
                raise AuthFailure(409, 40974, "此块正在由另一编辑器修改，本地输入已保留")
            if "rich" in block:
                rewrite_legacy_fragment(block, patch)
                if lease is None:
                    lease = SourceLease(draft_id=payload.id, block_id=patch.id, generation=0)
                    session.add(lease)
                lease.generation += 1
        text: Text = block["content"]
        if len(text):
            del text[:]
        text.insert(0, patch.content)
    if not changed:
        return payload.committed_seq
    state, update = doc.get_update(), doc.get_update(vector)
    if len(state) > MAX_STATE:
        raise AuthFailure(413, 41370, "协同文档已达容量限制，请导出后整理")
    fields = project(doc, policy)
    document.committed_seq += 1
    document.state, document.updated_at = state, now
    document.media_ids = sorted(set(document.media_ids) | references(fields))
    session.add(
        CollaborationUpdate(
            draft_id=payload.id,
            update_id=payload.operation_id,
            committed_seq=document.committed_seq,
            digest=hashlib.sha256(update).hexdigest(),
            payload=update,
            actor_id=actor.id,
            created_at=now,
        )
    )
    return document.committed_seq


def rewrite_legacy_fragment(block: Map[Any], patch: BlockSourcePatch) -> None:
    fragment = block["rich"]
    write_fragment(fragment, source_document(patch.content))
    block["rich_base"] = render_node(xml_document(fragment))
