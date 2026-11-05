"""Draft persistence primitives; transaction completion belongs to the use case."""

from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.draft_schemas import (
    DraftCatalog,
    DraftDetail,
    DraftListItem,
    DraftQuery,
    DraftSummary,
    TranslationMember,
)
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.repository import ContentRepository
from app.modules.content.text_preview import content_preview


def summary(row: ContentDraft) -> DraftSummary:
    return DraftSummary(
        id=row.id,
        type_id=row.type_id,
        title=row.title,
        excerpt=row.excerpt,
        preview=content_preview(row.body),
        author=row.author,
        revision=row.revision,
        status="trash" if row.deleted_at else "draft",
        is_demo=row.is_demo,
        published_revision=row.published_revision,
        public_path=row.public_path,
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        language=row.language,
        translation_group_id=row.translation_group_id,
    )


def detail(row: ContentDraft) -> DraftDetail:
    return DraftDetail.model_validate(
        {
            **summary(row).model_dump(),
            "body": row.body,
            "settings": row.editor_settings or {},
            "has_password": bool(row.access_password_hash),
        }
    )


class DraftRepository:
    def __init__(self, session: AsyncSession, config: Settings | None = None) -> None:
        self.session = session
        self.config = config

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
        if query.status == "published":
            filters.append(ContentDraft.published_revision.is_not(None))
        elif query.status == "draft":
            filters.append(ContentDraft.published_revision.is_(None))
        if query.language:
            filters.append(ContentDraft.language == query.language)
        if search := query.search.strip():
            filters.append(
                or_(
                    ContentDraft.title.icontains(search, autoescape=True),
                    ContentDraft.excerpt.icontains(search, autoescape=True),
                    ContentDraft.author.icontains(search, autoescape=True),
                    cast(ContentDraft.editor_settings["categories"], Text).icontains(
                        search, autoescape=True
                    ),
                    cast(ContentDraft.editor_settings["tags"], Text).icontains(
                        search, autoescape=True
                    ),
                    cast(
                        func.jsonb_path_query_array(
                            ContentDraft.body, cast("$[*].content", JSONPATH)
                        ),
                        Text,
                    ).icontains(search, autoescape=True),
                )
            )
        # Match every member first, then page group anchors. A translated search
        # hit must never turn into a duplicate row or an empty page.
        if query.grouped:
            matched = (
                select(ContentDraft.translation_group_id).where(*filters).distinct().correlate(None)
            )
            group_filter = TranslationGroup.id.in_(matched)
            total = await self.session.scalar(
                select(func.count()).select_from(TranslationGroup).where(group_filter)
            )
            selection = (
                select(ContentDraft)
                .join(TranslationGroup, TranslationGroup.source_id == ContentDraft.id)
                .where(group_filter)
            )
        else:
            total = await self.session.scalar(
                select(func.count()).select_from(ContentDraft).where(*filters)
            )
            selection = select(ContentDraft).where(*filters)
        rows = list(
            await self.session.scalars(
                selection.order_by(ContentDraft.updated_at.desc(), ContentDraft.id)
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            )
        )
        group_ids = [row.translation_group_id for row in rows]
        groups = {
            group.id: group
            for group in await self.session.scalars(
                select(TranslationGroup).where(TranslationGroup.id.in_(group_ids))
            )
        }
        members: dict[UUID, list[ContentDraft]] = {key: [] for key in group_ids}
        for member in await self.session.scalars(
            select(ContentDraft)
            .where(ContentDraft.translation_group_id.in_(group_ids))
            .order_by(ContentDraft.created_at, ContentDraft.id)
        ):
            members[member.translation_group_id].append(member)
        settings = [EditorSettings.model_validate(row.editor_settings or {}) for row in rows]
        covers = await editor_media(
            self.session,
            self.config,
            " ".join(
                f"site://media/{item.cover_media_id}" for item in settings if item.cover_media_id
            ),
            EditorSettings(),
            strict=False,
        )
        counts = await ContentRepository(self.session).comment_counts([row.id for row in rows])
        return DraftCatalog(
            items=[
                DraftListItem(
                    **summary(row).model_dump(),
                    cover_url=covers[str(meta.cover_media_id)].original
                    if str(meta.cover_media_id) in covers
                    else None,
                    categories=meta.categories,
                    tags=meta.tags,
                    pinned=meta.pinned,
                    comment_count=counts.get(row.id, 0),
                    group_revision=groups[row.translation_group_id].revision,
                    translations=[
                        TranslationMember(
                            **summary(member).model_dump(),
                            is_original=member.id == groups[row.translation_group_id].source_id,
                            needs_review=member.translated_from_revision is not None
                            and member.translated_from_revision
                            < (row.published_revision or row.revision),
                        )
                        for member in members[row.translation_group_id]
                    ],
                )
                for row, meta in zip(rows, settings, strict=True)
            ],
            total=total or 0,
            page=query.page,
            page_size=query.page_size,
        )
