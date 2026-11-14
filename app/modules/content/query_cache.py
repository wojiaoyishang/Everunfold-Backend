"""Cache selected anonymous HTTP projections using the committed database version."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from time import perf_counter

from pydantic import BaseModel
from sqlalchemy import func, select

from app.core.query_cache import QueryRevision, Variant
from app.core.query_cache_metrics import count_query_sql
from app.core.query_cache_schemas import QueryBusiness, QueryOutcome
from app.core.runtime import Runtime
from app.modules.content.models import Content, SiteSettings
from app.modules.content.repository import ContentQuery
from app.modules.content.schemas import ContentSummary, Page, SiteDTO, TaxonomyKind, TaxonomyList
from app.modules.content.service import PublicContentService
from app.modules.settings.languages import LanguageCatalog, language_catalog, preferred_language


class PublicQueryCache:
    def __init__(self, runtime: Runtime, service: PublicContentService) -> None:
        self.cache = runtime.query_cache
        self.metrics = runtime.query_cache_metrics
        self.service = service
        self.session = service.repository.session

    async def _authority(
        self, *, include_languages: bool = False
    ) -> tuple[QueryRevision | None, LanguageCatalog | None]:
        statement = select(
            SiteSettings.source_epoch,
            SiteSettings.generation,
            SiteSettings.content_generation,
            func.statement_timestamp(),
        ).where(SiteSettings.id == 1)
        if include_languages:
            statement = statement.add_columns(
                SiteSettings.data["language_catalog"].label("catalog")
            )
        row = (await self.session.execute(statement)).one_or_none()
        if row is None:
            return None, None
        catalog = (
            LanguageCatalog.model_validate(row._mapping["catalog"] or {})
            if include_languages
            else None
        )
        return QueryRevision(str(row[0]), row[1], row[2], row[3]), catalog

    async def _next_opening(self) -> datetime | None:
        return await self.session.scalar(
            select(Content.opens_at)
            .where(
                Content.publication_status == "published",
                Content.visibility == "password",
                Content.access_password_hash.is_not(None),
                Content.opens_at > func.now(),
            )
            .order_by(Content.opens_at)
            .limit(1)
        )

    async def _latest_revision(self) -> QueryRevision | None:
        revision, _ = await self._authority()
        return revision

    async def _observe[T: BaseModel](
        self, business: QueryBusiness, run: Callable[[], Awaitable[tuple[T, QueryOutcome]]]
    ) -> T:
        started = perf_counter()
        outcome: QueryOutcome = "error"
        with count_query_sql() as sql:
            try:
                result, outcome = await run()
                return result
            finally:
                self.metrics.record(business, outcome, (perf_counter() - started) * 1000, sql.count)

    async def _projection[T: BaseModel](
        self,
        business: QueryBusiness,
        model: type[T],
        variant: Variant,
        load: Callable[[], Awaitable[T]],
    ) -> T:
        async def run() -> tuple[T, QueryOutcome]:
            if self.cache.client is None:
                return await load(), "bypass"
            if self.cache.unavailable:
                return await load(), "error"
            return await self.cache.read(
                business,
                model,
                await self._latest_revision(),
                variant,
                load,
                self._latest_revision,
                self._next_opening,
            )

        return await self._observe(business, run)

    async def site(self) -> SiteDTO:
        return await self._projection("site", SiteDTO, (), self.service.site)

    async def taxonomies(self, kind: TaxonomyKind | None) -> TaxonomyList:
        async def load() -> TaxonomyList:
            return TaxonomyList(items=await self.service.taxonomies(kind))

        return await self._projection("taxonomies", TaxonomyList, (kind,), load)

    async def contents(self, query: ContentQuery, locale: str | None) -> Page[ContentSummary]:
        async def run() -> tuple[Page[ContentSummary], QueryOutcome]:
            revision = catalog = None
            common = (
                query.page <= 3
                and query.page_size == 10
                and not (query.q.strip() or query.category or query.tag)
            )
            if common and self.cache.client is not None and not self.cache.unavailable:
                revision, catalog = await self._authority(include_languages=True)
            if catalog is None:
                catalog = await language_catalog(self.session)
            language = query.language or preferred_language(catalog, locale)
            resolved = replace(query, language=language, default_language=catalog.default_code)

            async def load() -> Page[ContentSummary]:
                return await self.service.list_contents(resolved)

            if not common or not any(
                item.code == language and item.enabled for item in catalog.languages
            ):
                return await load(), "bypass"
            if self.cache.unavailable:
                return await load(), "error"
            return await self.cache.read(
                "content_list",
                Page[ContentSummary],
                revision,
                (resolved.page, resolved.kind, language, resolved.all_languages),
                load,
                self._latest_revision,
                self._next_opening,
            )

        return await self._observe("content_list", run)
