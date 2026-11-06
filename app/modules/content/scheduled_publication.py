"""Persisted one-shot runs. Queue ticks may repeat or disappear; PostgreSQL owns completion."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import Database
from app.modules.auth.credentials_models import ApiKey
from app.modules.auth.key_service import identity_by_digest
from app.modules.auth.models import User
from app.modules.auth.permissions import Actor, AuthSubject
from app.modules.auth.resource_access import require_content_access
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft
from app.modules.content.draft_repository import detail
from app.modules.content.draft_schemas import DraftDetail, DraftSchedule
from app.modules.content.merge_lock import active_merge
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.settings.models import SettingsAudit


def clear_schedule(row: ContentDraft, *, error: str | None = None) -> None:
    row.scheduled_for = row.scheduled_revision = row.scheduled_payload = None
    row.schedule_error = error


async def schedule_publication(
    session: AsyncSession,
    config: Settings,
    row: ContentDraft,
    command: DraftSchedule,
    actor: AuthSubject,
) -> None:
    if command.scheduled_for <= datetime.now(UTC):
        raise AuthFailure(422, 42257, "定时发布时间必须晚于当前时间")
    await ContentPublication(session, config).prepare(row, now=command.scheduled_for)
    row.scheduled_for, row.scheduled_revision = command.scheduled_for, row.revision
    row.schedule_error = None
    row.scheduled_payload = {
        "run_id": str(command.operation_id),
        "actor_id": str(actor.id),
        "key_id": str(actor.key_id) if isinstance(actor, Actor) and actor.key_id else None,
        "draft": detail(row).model_dump(mode="json"),
        # A captured password hash stays private, never in an API response or queue payload.
        "access_password_hash": row.access_password_hash,
    }


async def scheduled_actor(session: AsyncSession, row: ContentDraft) -> Actor:
    assert row.scheduled_payload is not None
    actor_id = UUID(row.scheduled_payload["actor_id"])
    actor: Actor | None = None
    if key_id := row.scheduled_payload.get("key_id"):
        key = await session.get(ApiKey, UUID(key_id))
        identity = await identity_by_digest(session, key.token_hash, lock=True) if key else None
        if identity and identity.key.audience == "everunfold" and identity.user.id == actor_id:
            actor = identity.actor()
    else:
        user = await session.scalar(
            select(User).where(User.id == actor_id).with_for_update(read=True)
        )
        if user:
            actor = Actor.browser(user)
    if actor is None:
        raise AuthFailure(403, 40364, "定时发布人的账号或凭据已失效")
    await require_content_access(session, actor, "content:publish", row.id)
    return actor


async def publish_scheduled(session: AsyncSession, config: Settings, draft_id: UUID) -> int:
    await lock_references(session)
    now = datetime.now(UTC)
    row = await session.scalar(
        select(ContentDraft)
        .where(ContentDraft.id == draft_id, ContentDraft.scheduled_for <= now)
        .with_for_update()
    )
    if row is None:
        return 0
    if row.deleted_at:
        clear_schedule(row)
        await session.commit()
        return 0
    if await active_merge(session, row.id):
        # Merging freezes the latest revision. Leave the registered run intact for the next tick.
        return 0
    assert row.scheduled_payload is not None and row.scheduled_for is not None
    payload, planned_at, revision = row.scheduled_payload, row.scheduled_for, row.scheduled_revision
    error = None
    try:
        # A failed public projection must not leave partially changed Content/Search/Outbox rows.
        async with session.begin_nested():
            await scheduled_actor(session, row)
            frozen = DraftDetail.model_validate(payload["draft"])
            captured = ContentDraft(
                id=row.id,
                type_id=frozen.type_id,
                language=frozen.language,
                translation_group_id=row.translation_group_id,
                title=frozen.title,
                excerpt=frozen.excerpt,
                body=[block.model_dump(mode="json") for block in frozen.body],
                author=frozen.author,
                revision=frozen.revision,
                public_path=frozen.public_path,
                published_at=frozen.published_at,
                editor_settings=frozen.settings.model_dump(mode="json"),
                access_password_hash=payload["access_password_hash"],
            )
            publication = ContentPublication(session, config)
            await publication.publish(captured, published_at=planned_at)
            await publication.stage()
            row.published_revision = captured.published_revision
            row.published_at, row.public_path = captured.published_at, captured.public_path
    except AuthFailure as failure:
        error = (
            "permission_changed"
            if failure.status in {401, 403, 404}
            else "path_conflict"
            if failure.code == 40954
            else "content_invalid"
        )
    clear_schedule(row, error=error)
    session.add(
        SettingsAudit(
            user_id=UUID(payload["actor_id"]),
            resource="content-publication",
            revision=revision,
            changes={
                "action": "scheduled_failed" if error else "scheduled_published",
                "draft_id": str(draft_id),
                "run_id": payload["run_id"],
                "planned_at": planned_at.isoformat(),
                "error": error,
            },
        )
    )
    await session.commit()
    return int(error is None)


async def publish_due(config: Settings) -> int:
    database = Database(config.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            ids = list(
                await session.scalars(
                    select(ContentDraft.id)
                    .where(ContentDraft.scheduled_for <= datetime.now(UTC))
                    .order_by(ContentDraft.scheduled_for, ContentDraft.id)
                    .limit(100)
                )
            )
        completed = 0
        for draft_id in ids:
            async with database.session_factory() as session:
                completed += await publish_scheduled(session, config, draft_id)
        return completed
    finally:
        await database.close()
