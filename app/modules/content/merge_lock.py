"""All article writers take the draft row lock before checking this durable lease."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import DraftMerge
from app.modules.content.version_schemas import MergeLock


async def active_merge(session: AsyncSession, draft_id: UUID) -> DraftMerge | None:
    return await session.scalar(
        select(DraftMerge).where(
            DraftMerge.draft_id == draft_id,
            DraftMerge.status == "active",
            DraftMerge.expires_at > datetime.now(UTC),
        )
    )


async def require_unlocked(
    session: AsyncSession, draft_id: UUID, *, owner: UUID | None = None
) -> None:
    locked = await active_merge(session, draft_id)
    if locked and locked.id != owner:
        raise AuthFailure(
            409, 40980, "正在合并版本，最新版暂时只读；尚未确认的输入已保留，请恢复为草稿"
        )


async def lock_state(session: AsyncSession, draft_id: UUID) -> MergeLock | None:
    locked = await active_merge(session, draft_id)
    if locked is None:
        return None
    actor = await session.get(User, locked.actor_id)
    return MergeLock(
        id=locked.id,
        actor_id=locked.actor_id,
        actor=actor.display_name if actor else "编辑者",
        target_seq=locked.target_seq,
        expires_at=locked.expires_at,
    )
