"""Public comment access follows the same published-content and page gates as reading."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def public_page_ids(session: AsyncSession) -> list[UUID]:
    from app.modules.pages.service import public_pages

    return [page.id for page in await public_pages(session) if page.access == "public"]
