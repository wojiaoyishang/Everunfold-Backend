"""Draft persistence primitives; transaction completion belongs to the use case."""

from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select, true
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.content.component_document import ComponentDocument
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.draft_schemas import (
    DraftAuthor,
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
    components = (
        ComponentDocument.model_validate(row.component_document)
        if row.edit_mode == "components"
        else None
    )
    return DraftSummary(
        edit_mode="components" if components is not None else "blocks",
        owner_id=row.created_by,
        id=row.id,
        type_id=row.type_id,
        title=row.title,
        excerpt=row.excerpt,
        preview=content_preview(
            [{"content": components.text()}] if components is not None else row.body
        ),
        author=row.author,
        revision=row.revision,
        status="trash" if row.deleted_at else "draft",
        is_demo=row.is_demo,
        published_revision=row.published_revision,
        public_path=row.public_path,
        published_at=row.published_at,
        scheduled_for=row.scheduled_for,
        scheduled_revision=row.scheduled_revision,
        schedule_error=row.schedule_error,
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
            "component_document": row.component_document,
            "settings": row.editor_settings or {},
            "has_password": bool(row.access_password_hash),
        }
    )


class DraftRepository:
    def __init__(
        self, session: AsyncSession, config: Settings | None = None, *, owner_id: UUID | None = None
    ) -> None:
        self.session = session
        self.config = config
        self.owner_id = owner_id

    async def find(self, draft_id: UUID, *, lock: bool = False) -> ContentDraft | None:
        query = select(ContentDraft).where(ContentDraft.id == draft_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def catalog(self, query: DraftQuery) -> DraftCatalog:
        filters = [ContentDraft.type_id == query.type_id]
        if self.owner_id is not None:
            from sqlalchemy.orm import aliased

            ownership_member = aliased(ContentDraft)
            foreign_member = (
                select(ownership_member.id)
                .where(
                    ownership_member.translation_group_id == ContentDraft.translation_group_id,
                    ownership_member.created_by != self.owner_id,
                )
                .exists()
            )
            filters.extend([ContentDraft.created_by == self.owner_id, ~foreign_member])
        filters.append(
            ContentDraft.deleted_at.is_not(None)
            if query.status == "trash"
            else ContentDraft.deleted_at.is_(None)
        )
        if query.status == "published":
            filters.append(ContentDraft.published_revision.is_not(None))
        elif query.status == "draft":
            filters.append(ContentDraft.published_revision.is_(None))
        elif query.status == "scheduled":
            filters.append(ContentDraft.scheduled_for.is_not(None))
        elif query.status == "failed":
            filters.append(ContentDraft.schedule_error.is_not(None))
        if query.author_id:
            filters.append(ContentDraft.created_by == query.author_id)
        if query.category:
            filters.append(ContentDraft.editor_settings["categories"].contains([query.category]))
        if query.tag:
            filters.append(ContentDraft.editor_settings["tags"].contains([query.tag]))
        if query.updated_from:
            filters.append(ContentDraft.updated_at >= query.updated_from)
        if query.updated_to:
            filters.append(ContentDraft.updated_at <= query.updated_to)
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
                    (
                        (ContentDraft.edit_mode == "components")
                        & cast(ContentDraft.component_document, Text).icontains(
                            search, autoescape=True
                        )
                    ),
                )
            )
        # Match every member first, then page group anchors. A translated search
        # hit must never turn into a duplicate row or an empty page.
        if query.grouped:
            matched = (
                select(ContentDraft.translation_group_id).where(*filters).distinct().correlate(None)
            )
            group_filter = TranslationGroup.id.in_(matched)
            selection = (
                select(ContentDraft)
                .join(TranslationGroup, TranslationGroup.source_id == ContentDraft.id)
                .where(group_filter)
                .where(
                    true()  # Keep the group anchor when only a translation was trashed.
                    if query.status == "trash"
                    else ContentDraft.deleted_at.is_(None)
                )
            )
            total = await self.session.scalar(
                select(func.count()).select_from(selection.subquery())
            )
        else:
            total = await self.session.scalar(
                select(func.count()).select_from(ContentDraft).where(*filters)
            )
            selection = select(ContentDraft).where(*filters)
        order_column = getattr(ContentDraft, query.order_by)
        order = order_column.asc() if query.direction == "asc" else order_column.desc()
        rows = list(
            await self.session.scalars(
                selection.order_by(order.nulls_last(), ContentDraft.id)
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
        from app.modules.content.collaboration.models import CollaborationDocument

        sequences = dict(
            (
                await self.session.execute(
                    select(
                        CollaborationDocument.draft_id, CollaborationDocument.committed_seq
                    ).where(
                        CollaborationDocument.draft_id.in_(
                            [item.id for group in members.values() for item in group]
                        )
                    )
                )
            ).all()
        )
        author_query = (
            select(ContentDraft.created_by, ContentDraft.author)
            .where(
                ContentDraft.type_id == query.type_id,
            )
            .distinct()
            .order_by(ContentDraft.author)
        )
        if self.owner_id:
            author_query = author_query.where(ContentDraft.created_by == self.owner_id)
        authors = {
            identity: name for identity, name in (await self.session.execute(author_query)).all()
        }
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
                    **{**summary(row).model_dump(), "collaboration_seq": sequences.get(row.id)},
                    cover_url=covers[str(meta.cover_media_id)].original
                    if str(meta.cover_media_id) in covers
                    else None,
                    categories=meta.categories,
                    tags=meta.tags,
                    pinned=meta.pinned,
                    comments_open=meta.comments_open,
                    comment_count=counts.get(row.id, 0),
                    group_revision=groups[row.translation_group_id].revision,
                    translations=[
                        TranslationMember(
                            **{
                                **summary(member).model_dump(),
                                "collaboration_seq": sequences.get(member.id),
                            },
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
            authors=[DraftAuthor(id=identity, name=name) for identity, name in authors.items()],
        )
