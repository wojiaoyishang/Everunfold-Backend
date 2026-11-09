"""Stage metadata in the existing Y.Doc; callers own locks, checks and commit."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pycrdt import Map, Text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.collaboration.document import decode, project
from app.modules.content.collaboration.models import CollaborationDocument, CollaborationUpdate
from app.modules.content.draft_models import ContentDraft, DraftRevision
from app.modules.content.draft_repository import detail
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.management_schemas import MetadataPatch, TermChange
from app.modules.content.taxonomy_names import TermDirectory, register_draft_terms


def change_terms(current: list[str], patch: TermChange | None) -> list[str]:
    if patch is None:
        return current
    if patch.mode == "replace":
        return patch.values
    if patch.mode == "add":
        return list(dict.fromkeys([*current, *patch.values]))
    removed = {value.casefold() for value in patch.values}
    return [value for value in current if value.casefold() not in removed]


async def stage_metadata(
    session: AsyncSession,
    row: ContentDraft,
    actor_id: UUID,
    patch: MetadataPatch,
    directory: TermDirectory,
    *,
    note: str,
) -> None:
    head = await session.get(CollaborationDocument, row.id, with_for_update=True)
    doc = decode(head.state) if head else None
    policy = EditorSettings.model_validate(row.editor_settings)
    fields = project(doc, policy) if doc else None
    current = fields.settings if fields else policy
    assert current is not None
    values = current.model_dump()
    for key in ("categories", "tags"):
        values[key] = change_terms(values[key], getattr(patch, key))
    for key in ("pinned", "comments_open"):
        if getattr(patch, key) is not None:
            values[key] = getattr(patch, key)
    settings = directory.normalize(EditorSettings.model_validate(values))
    await register_draft_terms(session, settings)
    now = datetime.now(UTC)
    if doc and head:
        vector = doc.get_state()
        meta = doc.get("meta", type=Map)
        with doc.transaction():
            for key in ("title", "excerpt"):
                value = getattr(patch, key)
                if value is not None and str(meta[key]) != value.strip():
                    text = meta[key]
                    assert isinstance(text, Text)
                    del text[:]
                    text.insert(0, value.strip())
            for key in ("categories", "tags", "pinned", "comments_open"):
                if getattr(current, key) != getattr(settings, key):
                    meta["settings"][key] = getattr(settings, key)
        update = doc.get_update(vector)
        if doc.get_update() != head.state:
            head.committed_seq += 1
            head.state = doc.get_update()
            head.updated_at = now
            session.add(
                CollaborationUpdate(
                    draft_id=row.id,
                    update_id=uuid4(),
                    committed_seq=head.committed_seq,
                    digest=hashlib.sha256(update).hexdigest(),
                    payload=update,
                    actor_id=actor_id,
                    created_at=now,
                )
            )
        fields = project(doc, policy)
        row.title, row.excerpt = fields.title, fields.excerpt
        row.body = [block.model_dump(mode="json") for block in fields.body]
    else:
        for key in ("title", "excerpt"):
            if getattr(patch, key) is not None:
                setattr(row, key, getattr(patch, key).strip())
    row.editor_settings = settings.model_dump(mode="json")
    row.revision += 1
    row.updated_at = now
    session.add(
        DraftRevision(
            draft_id=row.id,
            revision=row.revision,
            actor_id=actor_id,
            action="quick_edit",
            snapshot=detail(row).model_dump(mode="json"),
            created_at=now,
            note=note,
            epoch=head.epoch if head else None,
            committed_seq=head.committed_seq if head else None,
            parents=[row.revision - 1],
        )
    )
