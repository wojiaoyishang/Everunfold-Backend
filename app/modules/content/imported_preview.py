"""Explicitly withdraw the old import from functional testing, without deleting its data."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.models import Content
from app.modules.content.publication import ContentPublication
from app.modules.content.repository import public_conditions
from app.modules.media.locking import lock_references
from app.modules.settings.models import SettingsAudit


async def withdraw_imported_preview(
    session: AsyncSession, config: Settings, actor_id: UUID
) -> tuple[int, int | None]:
    """One transaction: old public rows, audit and complete replacement release together."""
    try:
        await lock_references(session)
        rows = list(
            await session.scalars(
                select(Content)
                .where(Content.source == "wordpress", *public_conditions())
                .order_by(Content.id)
                .with_for_update()
            )
        )
        if not rows:
            await session.rollback()
            return 0, None
        before = [
            {
                "id": str(row.id),
                "publication_status": row.publication_status,
                "public_generation": row.public_generation,
            }
            for row in rows
        ]
        for row in rows:
            row.publication_status = "withdrawn"
            row.public_generation += 1
        release = await ContentPublication(session, config).stage()
        session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="public-content",
                revision=release.revision,
                changes={
                    "action": "withdraw-imported-preview",
                    "source_epoch": str(release.source_epoch),
                    "before": before,
                },
            )
        )
        await session.commit()
        return len(rows), release.revision
    except Exception:
        await session.rollback()
        raise
