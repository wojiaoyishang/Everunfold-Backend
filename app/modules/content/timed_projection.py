"""Worker refreshes derived public snapshots after time-based access becomes public."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.models import Content
from app.modules.content.publication import ContentPublication
from app.modules.media.locking import lock_references
from app.modules.settings.cache_event_schema import CacheChange


async def refresh_opened_content(session: AsyncSession, config: Settings) -> int:
    now = datetime.now(UTC)
    due = (
        Content.publication_status == "published",
        Content.visibility == "password",
        Content.opens_at <= now,
        Content.open_synced_at.is_(None),
        Content.access_password_hash.is_not(None),
    )
    if await session.scalar(select(Content.id).where(*due).limit(1)) is None:
        return 0
    await lock_references(session)
    rows = list(await session.scalars(select(Content).where(*due).limit(100).with_for_update()))
    if not rows:
        await session.rollback()
        return 0
    for row in rows:
        row.open_synced_at = now
        row.public_generation += 1
    await ContentPublication(session, config).stage(
        *(CacheChange("content_opened", row.id, (row.path,)) for row in rows)
    )
    await session.commit()
    return len(rows)
