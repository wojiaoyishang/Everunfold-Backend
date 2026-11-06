"""Page paths are shared with article publishing under the reference transaction lock."""

from uuid import UUID

from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import Content
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


async def content_path_taken(session: AsyncSession, path: str) -> bool:
    return bool(
        await session.scalar(select(Content.id).where(Content.path == path).limit(1))
        or await session.scalar(
            select(ContentDraft.id)
            .where(
                or_(
                    ContentDraft.public_path == path,
                    ContentDraft.editor_settings["path"].astext == path,
                )
            )
            .limit(1)
        )
    )
