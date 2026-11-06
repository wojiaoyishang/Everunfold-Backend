"""Application-level public projections reused by HTTP and explicit snapshot export."""

import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.modules.content.access_policy import publicly_readable
from app.modules.content.draft_models import TranslationGroup
from app.modules.content.editor_schema import PublicEditorSettings
from app.modules.content.models import Comment, Content
from app.modules.content.repository import ContentQuery, ContentRepository, presentation_conditions
from app.modules.content.schemas import (
    ContentDetail,
    ContentKind,
    ContentSummary,
    ContentTranslation,
    Page,
    PublicComment,
    PublicSnapshot,
    SiteDTO,
    SiteStats,
    TaxonomyDTO,
    TaxonomyKind,
)


class PublicContentService:
    def __init__(self, repository: ContentRepository) -> None:
        self.repository = repository

    async def taxonomies(self, kind: TaxonomyKind | None = None) -> list[TaxonomyDTO]:
        return [
            TaxonomyDTO(
                id=str(taxonomy.id),
                slug=taxonomy.slug,
                name=taxonomy.name,
                kind=cast(TaxonomyKind, taxonomy.kind),
                count=count,
            )
            for taxonomy, count in await self.repository.taxonomies(kind)
        ]

    async def site(self) -> SiteDTO:
        stored = await self.repository.site_settings()
        if stored is None:
            raise HTTPException(status_code=503, detail="Public site has not been initialized")
        kind_counts = await self.repository.content_kind_counts()
        taxonomies = await self.taxonomies()
        stats = SiteStats(
            articles=kind_counts.get("article", 0),
            pages=kind_counts.get("page", 0),
            comments=await self.repository.public_comment_count(),
            tags=sum(item.kind == "tag" for item in taxonomies),
            categories=sum(item.kind == "category" for item in taxonomies),
        )
        data = {key: value for key, value in stored.data.items() if key in SiteDTO.model_fields}
        data["navigation"] = [
            {key: item[key] for key in ("label", "href", "icon") if key in item}
            for item in data.get("navigation", [])
        ]
        return SiteDTO.model_validate({**data, "stats": stats})

    @staticmethod
    def _summary(
        item: Content,
        taxonomy_map: dict[str, TaxonomyDTO],
        comment_counts: dict[uuid.UUID, int],
        *,
        authorized: bool = False,
    ) -> ContentSummary:
        related = sorted(
            (taxonomy_map[str(t.id)] for t in item.taxonomies if str(t.id) in taxonomy_map),
            key=lambda value: (value.name, value.id),
        )
        readable = authorized or publicly_readable(item)
        public_settings = PublicEditorSettings.model_validate(
            {
                key: value
                for key, value in (
                    (item.editor_settings or {}) if readable else {"comments_open": False}
                ).items()
                if key in {"comments_open", "seo_title", "seo_description"}
            }
        ).model_dump()
        return ContentSummary(
            access="public" if readable else "scheduled" if item.opens_at else "password",
            opens_at=item.opens_at,
            id=str(item.id),
            kind=cast(ContentKind, item.kind),
            title=item.title,
            path=item.path,
            excerpt=item.excerpt if readable else "",
            language=item.language,
            translation_group_id=str(item.translation_group_id or item.id),
            published_at=item.published_at,
            updated_at=item.updated_at,
            author=item.author,
            cover_url=item.cover_url if readable else None,
            reading_minutes=item.reading_minutes if readable else 1,
            word_count=item.word_count if readable else 0,
            categories=[t for t in related if t.kind == "category"],
            tags=[t for t in related if t.kind == "tag"],
            comment_count=comment_counts.get(item.id, 0) if readable else 0,
            pinned=item.pinned,
            **public_settings,
        )

    async def list_contents(self, query: ContentQuery) -> Page[ContentSummary]:
        contents, total = await self.repository.list_contents(query)
        taxonomy_map = {item.id: item for item in await self.taxonomies()}
        comment_counts = await self.repository.comment_counts([item.id for item in contents])
        items = [self._summary(item, taxonomy_map, comment_counts) for item in contents]
        await self._translations(items)
        return Page(
            items=items,
            page=query.page,
            page_size=query.page_size,
            total=total,
        )

    @staticmethod
    def _detail(
        item: Content,
        taxonomy_map: dict[str, TaxonomyDTO],
        comment_counts: dict[uuid.UUID, int],
        *,
        authorized: bool = False,
    ) -> ContentDetail:
        summary = PublicContentService._summary(
            item, taxonomy_map, comment_counts, authorized=authorized
        )
        readable = summary.access == "public"
        return ContentDetail.model_validate(
            {
                **summary.model_dump(),
                "content": item.content if readable else [],
                "media_urls": item.media_urls if readable else {},
            }
        )

    async def resolve(self, path: str) -> ContentDetail:
        item = await self.repository.resolve(path)
        if item is None:
            raise HTTPException(status_code=404)
        taxonomy_map = {item.id: item for item in await self.taxonomies()}
        counts = await self.repository.comment_counts([item.id])
        result = self._detail(item, taxonomy_map, counts)
        await self._translations([result])
        return result

    async def _translations(self, items: list[ContentSummary] | list[ContentDetail]) -> None:
        if not items:
            return
        keys = [uuid.UUID(item.translation_group_id or item.id) for item in items]
        rows = await self.repository.session.execute(
            select(Content, TranslationGroup.source_id)
            .outerjoin(TranslationGroup, TranslationGroup.id == Content.translation_group_id)
            .where(
                *presentation_conditions(),
                or_(Content.translation_group_id.in_(keys), Content.id.in_(keys)),
            )
            .order_by(Content.language, Content.id)
        )
        grouped: dict[str, list[ContentTranslation]] = {}
        for content, source_id in rows:
            key = str(content.translation_group_id or content.id)
            grouped.setdefault(key, []).append(
                ContentTranslation(
                    id=str(content.id),
                    language=content.language,
                    path=content.path,
                    access="public"
                    if publicly_readable(content)
                    else "scheduled"
                    if content.opens_at
                    else "password",
                    is_original=source_id is None or content.id == source_id,
                )
            )
        for item in items:
            item.translations = grouped.get(item.translation_group_id or item.id, [])

    @staticmethod
    def _comment_projections(
        comments: list[Comment], parents: dict[uuid.UUID, Comment] | None = None
    ) -> list[PublicComment]:
        public_by_id = {item.id: item for item in comments} if parents is None else parents
        results = []
        for item in comments:
            parent = public_by_id.get(item.parent_id) if item.parent_id else None
            if parent is not None and parent.content_id != item.content_id:
                parent = None
            results.append(
                PublicComment(
                    id=str(item.id),
                    parent_id=str(parent.id) if parent else None,
                    author=item.author,
                    content=item.content,
                    created_at=item.created_at,
                    reply_to=parent.author if parent else None,
                )
            )
        return results

    async def comments(
        self, content_id: uuid.UUID, *, page: int, page_size: int
    ) -> Page[PublicComment]:
        if not await self.repository.has_public_content(content_id):
            raise HTTPException(status_code=404)
        rows, parents, total = await self.repository.comments(
            content_id, page=page, page_size=page_size
        )
        return Page(
            items=self._comment_projections(rows, parents),
            page=page,
            page_size=page_size,
            total=total,
        )

    async def snapshot(self) -> PublicSnapshot:
        """Run inside a caller-owned REPEATABLE READ transaction for consistent export."""
        stored = await self.repository.site_settings()
        if stored is None:
            raise HTTPException(status_code=503, detail="Public site has not been initialized")
        contents = await self.repository.all_contents()
        taxonomies = await self.taxonomies()
        taxonomy_map = {item.id: item for item in taxonomies}
        counts = await self.repository.comment_counts([item.id for item in contents])
        comment_map: dict[str, list[PublicComment]] = {str(item.id): [] for item in contents}
        comments = await self.repository.all_comments()
        for row, projection in zip(comments, self._comment_projections(comments), strict=True):
            comment_map[str(row.content_id)].append(projection)
        details = [self._detail(item, taxonomy_map, counts) for item in contents]
        await self._translations(details)
        from app.modules.pages.service import public_pages

        return PublicSnapshot(
            source_epoch=str(stored.source_epoch),
            generation=stored.content_generation,
            exported_at=datetime.now(UTC),
            site=await self.site(),
            contents=details,
            comments=comment_map,
            taxonomies=taxonomies,
            pages=await public_pages(self.repository.session),
        )
