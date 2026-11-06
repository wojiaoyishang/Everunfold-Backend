"""Published navigation choices exclude bodies, drafts and internal metadata."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.models import Content
from app.modules.content.repository import presentation_conditions
from app.modules.pages.models import ManagedPage
from app.modules.settings.schemas import NavigationCatalog, NavigationQuery, NavigationResource


async def navigation_resources(session: AsyncSession, query: NavigationQuery) -> NavigationCatalog:
    search = query.search.strip()
    if query.kind == "page":
        title, path = ManagedPage.published["title"].astext, ManagedPage.published["path"].astext
        statement = select(
            ManagedPage.id, title.label("title"), path.label("path"), ManagedPage.language
        ).where(ManagedPage.deleted_at.is_(None), ManagedPage.published.is_not(None))
    else:
        title, path = Content.title, Content.path
        statement = select(Content.id, Content.title, Content.path, Content.language).where(
            Content.kind == query.kind, *presentation_conditions()
        )
    if search:
        statement = statement.where(
            (title.icontains(search, autoescape=True)) | (path.icontains(search, autoescape=True))
        )
    if query.id is not None:
        identity = ManagedPage.id if query.kind == "page" else Content.id
        statement = statement.where(identity == query.id)
    total = await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = (
        await session.execute(
            statement.order_by(title, path)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    return NavigationCatalog(
        items=[
            NavigationResource(
                kind=query.kind, id=row.id, title=row.title, path=row.path, language=row.language
            )
            for row in rows
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def public_navigation(
    session: AsyncSession, navigation: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Resolve the flat public API view as well as the frontend's richer settings tree."""
    result: list[dict[str, Any]] = []
    for item in navigation:
        reference = item.get("reference")
        href = item.get("href", "")
        if reference:
            if reference["kind"] == "page":
                href = await session.scalar(
                    select(ManagedPage.published["path"].astext).where(
                        ManagedPage.id == reference["id"],
                        ManagedPage.deleted_at.is_(None),
                        ManagedPage.published.is_not(None),
                    )
                )
            else:
                href = await session.scalar(
                    select(Content.path).where(
                        Content.id == reference["id"],
                        Content.kind == reference["kind"],
                        *presentation_conditions(),
                    )
                )
            if href is None:
                continue
        result.append(
            {
                "label": item["label"],
                "href": href,
                **({"icon": item["icon"]} if "icon" in item else {}),
            }
        )
    return result
