"""Real transaction/CRDT checks in the dedicated rollback-only test database."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, load_settings
from app.modules.auth.models import User
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.document import decode, project, seed
from app.modules.content.collaboration.models import CollaborationDocument, CollaborationUpdate
from app.modules.content.draft_models import ContentDraft
from app.modules.content.draft_repository import DraftRepository
from app.modules.content.draft_schemas import DraftBlock, DraftCreate, DraftFields, DraftQuery
from app.modules.content.draft_service import DraftService
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.management_schemas import (
    DraftBatch,
    DraftVersion,
    MetadataPatch,
    TaxonomyCommand,
    TaxonomyQuery,
    TermChange,
)
from app.modules.content.management_service import mutate_batch
from app.modules.content.models import Taxonomy
from app.modules.content.taxonomy_names import TermDirectory
from app.modules.content.taxonomy_service import TaxonomyService
from tests.test_public_content_integration import anyio_backend  # noqa: F401
from tests.test_public_content_integration import database_session as _database_session

database_session = _database_session

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Explicit real DB tests"
    ),
]


async def setup(session: AsyncSession) -> tuple[Actor, ContentDraft, ContentDraft]:
    user = User(
        id=uuid4(),
        username="workflow-" + uuid4().hex,
        display_name="Workflow",
        password_hash="unused",
        role="admin",
        active=True,
        created_at=datetime.now(UTC),
        must_change_password=False,
    )
    session.add(user)
    await session.flush()
    actor = Actor.browser(user)
    rows = []
    for title in ("Alpha", "Zulu"):
        result = await DraftService(session).mutate(
            "create",
            DraftCreate(
                operation_id=uuid4(),
                type_id="article",
                fields=DraftFields(
                    title=title,
                    excerpt="Summary",
                    body=[DraftBlock(id=uuid4(), content="正文保持")],
                    settings=EditorSettings(tags=["TagOne"]),
                ),
            ),
            actor,
        )
        row = await session.get(ContentDraft, result.id)
        assert row
        rows.append(row)
    return actor, rows[0], rows[1]


def config() -> Settings:
    return load_settings(os.environ.get("TEST_CONFIG_FILE", "config.test.local.toml")).model_copy(
        update={"frontends": {}}
    )


async def test_batch_preserves_crdt_body_and_epoch_and_retries_once(
    database_session: AsyncSession,
) -> None:
    session = database_session
    actor, first, second = await setup(session)
    state = seed(
        DraftFields(
            title=first.title,
            excerpt=first.excerpt,
            body=first.body,
            settings=EditorSettings.model_validate(first.editor_settings),
        ),
        source_first=True,
    ).get_update()
    head = CollaborationDocument(
        draft_id=first.id,
        epoch=uuid4(),
        committed_seq=0,
        initial_state=state,
        state=state,
        media_ids=[],
        updated_at=datetime.now(UTC),
        source_first=True,
        initial_seq=0,
    )
    session.add(head)
    await session.flush()
    epoch, body = head.epoch, first.body.copy()
    payload = DraftBatch(
        operation_id=uuid4(),
        action="edit",
        items=[
            DraftVersion(id=first.id, expected_revision=first.revision, expected_seq=0),
            DraftVersion(id=second.id, expected_revision=second.revision),
        ],
        patch=MetadataPatch(pinned=True, tags=TermChange(mode="add", values=["New"])),
    )
    result = await mutate_batch(session, config(), actor, payload)
    assert all(item.settings.pinned for item in result.items)
    assert head.epoch == epoch and head.committed_seq == 1 and first.body == body
    assert project(decode(head.state)).settings.tags == ["TagOne", "New"]
    update = await session.scalar(
        select(CollaborationUpdate).where(CollaborationUpdate.draft_id == first.id)
    )
    replica = decode(state)
    replica.apply_update(update.payload)
    assert project(replica).settings.pinned
    assert await mutate_batch(session, config(), actor, payload) == result
    assert head.committed_seq == 1 and first.revision == 2
    await mutate_batch(
        session,
        config(),
        actor,
        DraftBatch(
            operation_id=uuid4(),
            action="edit",
            items=[DraftVersion(id=first.id, expected_revision=2, expected_seq=1)],
            patch=MetadataPatch(excerpt=""),
        ),
    )
    assert project(decode(head.state)).excerpt == ""
    assert head.committed_seq == 2


async def test_entire_batch_rejects_live_conflicts_and_foreign_ownership(
    database_session: AsyncSession,
) -> None:
    session = database_session
    actor, first, second = await setup(session)
    payload = DraftBatch(
        operation_id=uuid4(),
        action="edit",
        items=[
            DraftVersion(id=first.id, expected_revision=1),
            DraftVersion(id=second.id, expected_revision=99),
        ],
        patch=MetadataPatch(comments_open=False),
    )
    with pytest.raises(AuthFailure):
        await mutate_batch(session, config(), actor, payload)
    assert first.revision == 1 and second.revision == 1 and first.editor_settings["comments_open"]
    foreign = Actor(
        id=uuid4(),
        username="Other",
        display_name="Other",
        role="user",
        permissions={"content:write": "own"},
    )
    with pytest.raises(AuthFailure):
        await mutate_batch(session, config(), foreign, payload)
    assert first.revision == 1


async def test_taxonomy_rename_merge_removal_and_impact_guard(
    database_session: AsyncSession,
) -> None:
    session = database_session
    actor, first, _ = await setup(session)
    service = TaxonomyService(session, config())
    row = await session.scalar(
        select(Taxonomy).where(Taxonomy.kind == "tag", Taxonomy.name == "TagOne")
    )
    assert row
    created_id = row.id
    slug = row.slug
    change = TaxonomyCommand(
        operation_id=uuid4(),
        action="update",
        id=row.id,
        expected_revision=row.revision,
        kind="tag",
        name="Renamed",
    )
    impact = await service.preview(change)
    assert impact.drafts == 2
    with pytest.raises(AuthFailure):
        await service.mutate(actor, change)
    changed = await service.mutate(actor, change.model_copy(update={"impact_token": impact.token}))
    assert changed.id == created_id and row.slug == slug
    assert first.editor_settings["tags"] == ["Renamed"]
    assert (await TermDirectory.load(session)).names_for("tag", ["TagOne"]) == ["Renamed"]
    target = await service.mutate(
        actor, TaxonomyCommand(operation_id=uuid4(), action="create", kind="tag", name="Target")
    )
    merge = TaxonomyCommand(
        operation_id=uuid4(),
        action="merge",
        id=row.id,
        expected_revision=row.revision,
        kind="tag",
        target_id=target.id,
        expected_target_revision=1,
    )
    merge.impact_token = (await service.preview(merge)).token
    await service.mutate(actor, merge)
    assert first.editor_settings["tags"] == ["Target"]
    target_row = await session.get(Taxonomy, target.id)
    remove = TaxonomyCommand(
        operation_id=uuid4(),
        action="remove",
        id=target.id,
        expected_revision=target_row.revision,
        kind="tag",
    )
    remove.impact_token = (await service.preview(remove)).token
    await service.mutate(actor, remove)
    assert first.editor_settings["tags"] == []
    assert (await TermDirectory.load(session)).names_for(
        "tag", ["TagOne", "Renamed", "Target"]
    ) == []
    catalog = await service.catalog(TaxonomyQuery(kind="tag"))
    assert all(item.id not in {row.id, target.id} for item in catalog.items)


async def test_exact_filters_and_stable_title_sort(database_session: AsyncSession) -> None:
    session = database_session
    actor, first, second = await setup(session)
    repository = DraftRepository(session, owner_id=actor.id)
    result = await repository.catalog(
        DraftQuery(
            type_id="article", author_id=actor.id, tag="TagOne", order_by="title", direction="asc"
        )
    )
    assert [item.id for item in result.items] == [first.id, second.id]
    assert (await repository.catalog(DraftQuery(type_id="article", tag="Tag"))).total == 0
    assert (await repository.catalog(DraftQuery(type_id="article", status="scheduled"))).total == 0


def test_taxonomy_commands_do_not_bypass_impact_by_claiming_create() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaxonomyCommand(
            operation_id=uuid4(), action="create", id=uuid4(), expected_revision=1, name="Name"
        )
    with pytest.raises(ValidationError):
        TaxonomyCommand(operation_id=uuid4(), action="create", name=" ")


async def test_trash_catalog_retains_original_anchor_for_a_trashed_translation(
    database_session: AsyncSession,
) -> None:
    session = database_session
    _, original, translated = await setup(session)
    translated.translation_group_id = original.translation_group_id
    translated.language = "en"
    translated.deleted_at = datetime.now(UTC)
    await session.flush()
    catalog = await DraftRepository(session, config()).catalog(
        DraftQuery(type_id="article", status="trash", search=translated.title)
    )
    group = next(item for item in catalog.items if item.id == original.id)
    assert group.status == "draft"
    assert next(item for item in group.translations if item.id == translated.id).status == "trash"
