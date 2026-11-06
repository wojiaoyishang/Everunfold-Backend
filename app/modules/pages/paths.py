"""Page paths are shared with article publishing under the reference transaction lock."""

from uuid import UUID

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pages.models import ManagedPage


async def page_path_taken(session: AsyncSession, path: str, excluding: UUID | None = None) -> bool:
    return (
        await session.scalar(
            select(ManagedPage.id)
            .where(
                ManagedPage.deleted_at.is_(None),
                ManagedPage.id != excluding if excluding else true(),
                or_(
                    ManagedPage.data["path"].astext == path,
                    ManagedPage.published["path"].astext == path,
                ),
            )
            .limit(1)
        )
        is not None
    )
