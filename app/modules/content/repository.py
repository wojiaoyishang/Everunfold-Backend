"""Public-only database reads. This layer never commits application transactions."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.modules.content.models import (
    Comment,
    Content,
    ContentTaxonomy,
    SearchDocument,
    SiteSettings,
    Taxonomy,
)
from app.modules.content.schemas import ContentKind, TaxonomyKind


def public_conditions() -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return Content.publication_status == "published", or_(
        Content.visibility == "public",
        (Content.visibility == "password")
        & Content.access_password_hash.is_not(None)
        & (Content.opens_at <= func.now()),
    )


def presentation_conditions() -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return Content.publication_status == "published", or_(
        Content.visibility == "public",
        (Content.visibility == "password") & Content.access_password_hash.is_not(None),
    )


@dataclass(frozen=True, slots=True)
class ContentQuery:
    page: int = 1
    page_size: int = 10
    q: str = ""
    kind: ContentKind | None = None
    category: str | None = None
    tag: str | None = None


class ContentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _content_selection(query: ContentQuery) -> Select[Content]:
        statement = select(Content).where(*presentation_conditions())
        if query.kind:
            statement = statement.where(Content.kind == query.kind)
        for kind, slug in (("category", query.category), ("tag", query.tag)):
            if slug:
                statement = statement.where(
                    Content.taxonomies.any((Taxonomy.kind == kind) & (Taxonomy.slug == slug))
                )
        if query.q.strip():
            # Escape LIKE metacharacters so a visitor's search stays literal and bounded.
            term = query.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{term}%"
            statement = statement.join(SearchDocument).where(
                or_(
                    SearchDocument.title.ilike(pattern, escape="\\"),
                    public_conditions()[1] & SearchDocument.body.ilike(pattern, escape="\\"),
                )
            )
        return statement

    async def list_contents(self, query: ContentQuery) -> tuple[list[Content], int]:
        statement = self._content_selection(query)
        total = int(
            await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        statement = (
            statement.options(selectinload(Content.taxonomies))
            .order_by(Content.pinned.desc(), Content.published_at.desc(), Content.id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        return list(await self.session.scalars(statement)), total

    async def resolve(self, path: str) -> Content | None:
        return await self.session.scalar(
            select(Content)
            .where(Content.path == path, *presentation_conditions())
            .options(selectinload(Content.taxonomies))
        )

    async def has_public_content(self, content_id: uuid.UUID) -> bool:
        result = await self.session.scalar(
            select(Content.id).where(Content.id == content_id, *public_conditions())
        )
        return result is not None

    async def all_contents(self) -> list[Content]:
        statement = (
            select(Content)
            .where(*presentation_conditions())
            .options(selectinload(Content.taxonomies))
            .order_by(Content.pinned.desc(), Content.published_at.desc(), Content.id)
        )
        return list(await self.session.scalars(statement))

    async def public_ids(self, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        statement = select(Content.id).where(Content.id.in_(ids), *public_conditions())
        return list(await self.session.scalars(statement))

    async def taxonomies(self, kind: TaxonomyKind | None = None) -> list[tuple[Taxonomy, int]]:
        statement = (
            select(Taxonomy, func.count(Content.id))
            .join(ContentTaxonomy, ContentTaxonomy.taxonomy_id == Taxonomy.id)
            .join(Content, Content.id == ContentTaxonomy.content_id)
            .where(*public_conditions())
            .group_by(Taxonomy.id)
            .order_by(Taxonomy.name, Taxonomy.id)
        )
        if kind:
            statement = statement.where(Taxonomy.kind == kind)
        rows = await self.session.execute(statement)
        return [(row[0], int(row[1])) for row in rows]

    async def comment_counts(self, content_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not content_ids:
            return {}
        rows = await self.session.execute(
            select(Comment.content_id, func.count(Comment.id))
            .where(Comment.content_id.in_(content_ids), Comment.status == "approved")
            .group_by(Comment.content_id)
        )
        return {row[0]: int(row[1]) for row in rows}

    async def comments(
        self, content_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[Comment], dict[uuid.UUID, Comment], int]:
        filters = Comment.content_id == content_id, Comment.status == "approved"
        total = int(await self.session.scalar(select(func.count(Comment.id)).where(*filters)) or 0)
        rows = list(
            await self.session.scalars(
                select(Comment)
                .where(*filters)
                .order_by(Comment.created_at, Comment.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        parent_ids = [item.parent_id for item in rows if item.parent_id is not None]
        parents = list(
            await self.session.scalars(select(Comment).where(*filters, Comment.id.in_(parent_ids)))
        )
        return rows, {item.id: item for item in parents}, total

    async def all_comments(self) -> list[Comment]:
        return list(
            await self.session.scalars(
                select(Comment)
                .join(Content, Content.id == Comment.content_id)
                .where(Comment.status == "approved", *public_conditions())
                .order_by(Comment.created_at, Comment.id)
            )
        )

    async def site_settings(self) -> SiteSettings | None:
        return await self.session.get(SiteSettings, 1)

    async def content_kind_counts(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(Content.kind, func.count(Content.id))
            .where(*public_conditions())
            .group_by(Content.kind)
        )
        return {row[0]: int(row[1]) for row in rows}

    async def public_comment_count(self) -> int:
        result = await self.session.scalar(
            select(func.count(Comment.id))
            .join(Content, Content.id == Comment.content_id)
            .where(Comment.status == "approved", *public_conditions())
        )
        return int(result or 0)
