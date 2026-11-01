"""Application-level public projections reused by HTTP and explicit snapshot export."""

import uuid
from datetime import UTC, datetime
from typing import cast

from fastapi import HTTPException

from app.modules.content.models import Comment, Content
from app.modules.content.repository import ContentQuery, ContentRepository
from app.modules.content.schemas import (
    ContentDetail,
    ContentKind,
    ContentSummary,
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
        item: Content, taxonomy_map: dict[str, TaxonomyDTO], comment_counts: dict[uuid.UUID, int]
    ) -> ContentSummary:
        related = sorted(
            (taxonomy_map[str(t.id)] for t in item.taxonomies if str(t.id) in taxonomy_map),
            key=lambda value: (value.name, value.id),
        )
        return ContentSummary(
            id=str(item.id),
            kind=cast(ContentKind, item.kind),
            title=item.title,
            path=item.path,
            excerpt=item.excerpt,
            language=item.language,
            published_at=item.published_at,
            updated_at=item.updated_at,
            author=item.author,
            cover_url=item.cover_url,
            reading_minutes=item.reading_minutes,
            word_count=item.word_count,
            categories=[t for t in related if t.kind == "category"],
            tags=[t for t in related if t.kind == "tag"],
            comment_count=comment_counts.get(item.id, 0),
            pinned=item.pinned,
        )

    async def list_contents(self, query: ContentQuery) -> Page[ContentSummary]:
        contents, total = await self.repository.list_contents(query)
        taxonomy_map = {item.id: item for item in await self.taxonomies()}
        comment_counts = await self.repository.comment_counts([item.id for item in contents])
        return Page(
            items=[self._summary(item, taxonomy_map, comment_counts) for item in contents],
            page=query.page,
            page_size=query.page_size,
            total=total,
        )

    @staticmethod
    def _detail(
        item: Content, taxonomy_map: dict[str, TaxonomyDTO], comment_counts: dict[uuid.UUID, int]
    ) -> ContentDetail:
        summary = PublicContentService._summary(item, taxonomy_map, comment_counts)
        return ContentDetail.model_validate(
            {**summary.model_dump(), "content": item.content, "media_urls": item.media_urls}
        )

    async def resolve(self, path: str) -> ContentDetail:
        item = await self.repository.resolve(path)
        if item is None:
            raise HTTPException(status_code=404)
        taxonomy_map = {item.id: item for item in await self.taxonomies()}
        counts = await self.repository.comment_counts([item.id])
        return self._detail(item, taxonomy_map, counts)

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
        return PublicSnapshot(
            source_epoch=str(stored.source_epoch),
            generation=stored.generation,
            exported_at=datetime.now(UTC),
            site=await self.site(),
            contents=[self._detail(item, taxonomy_map, counts) for item in contents],
            comments=comment_map,
            taxonomies=taxonomies,
        )
