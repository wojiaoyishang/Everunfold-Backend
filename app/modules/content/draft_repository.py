"""Draft persistence primitives; transaction completion belongs to the use case."""

from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.draft_models import ContentDraft
from app.modules.content.draft_schemas import DraftCatalog, DraftDetail, DraftQuery, DraftSummary


def summary(row: ContentDraft) -> DraftSummary:
    return DraftSummary(
        id=row.id,
        type_id=row.type_id,
        title=row.title,
        excerpt=row.excerpt,
        preview=" ".join(" ".join(str(block.get("content", "")) for block in row.body).split())[
            :180
        ],
        author=row.author,
        revision=row.revision,
        status="trash" if row.deleted_at else "draft",
        is_demo=row.is_demo,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def detail(row: ContentDraft) -> DraftDetail:
    return DraftDetail.model_validate({**summary(row).model_dump(), "body": row.body})


class DraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, draft_id: UUID, *, lock: bool = False) -> ContentDraft | None:
        query = select(ContentDraft).where(ContentDraft.id == draft_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def catalog(self, query: DraftQuery) -> DraftCatalog:
        filters = [ContentDraft.type_id == query.type_id]
        filters.append(
            ContentDraft.deleted_at.is_not(None)
            if query.status == "trash"
            else ContentDraft.deleted_at.is_(None)
        )
        if search := query.search.strip():
            filters.append(
                or_(
                    ContentDraft.title.icontains(search, autoescape=True),
                    ContentDraft.excerpt.icontains(search, autoescape=True),
                    cast(
                        func.jsonb_path_query_array(
                            ContentDraft.body, cast("$[*].content", JSONPATH)
                        ),
                        Text,
                    ).icontains(search, autoescape=True),
                )
            )
        total = await self.session.scalar(
            select(func.count()).select_from(ContentDraft).where(*filters)
        )
        rows = await self.session.scalars(
            select(ContentDraft)
            .where(*filters)
            .order_by(ContentDraft.updated_at.desc(), ContentDraft.id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
        return DraftCatalog(
            items=[summary(row) for row in rows],
            total=total or 0,
            page=query.page,
            page_size=query.page_size,
        )
