"""HTTP transport for the public content service."""

from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime import Runtime
from app.modules.content.repository import ContentQuery, ContentRepository
from app.modules.content.schemas import (
    ContentDetail,
    ContentKind,
    ContentSummary,
    Page,
    PublicComment,
    PublicResponse,
    SiteDTO,
    TaxonomyKind,
    TaxonomyList,
)
from app.modules.content.service import PublicContentService

router = APIRouter(prefix="/v1", tags=["public-content"])


def runtime_from_request(request: Request) -> Runtime:
    runtime = cast(Runtime | None, getattr(request.app.state, "runtime", None))
    if runtime is None:
        raise HTTPException(status_code=503)
    return runtime


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    runtime = runtime_from_request(request)
    async with runtime.database.session_factory() as session:
        yield session


def get_service(session: Annotated[AsyncSession, Depends(get_session)]) -> PublicContentService:
    return PublicContentService(ContentRepository(session))


Service = Annotated[PublicContentService, Depends(get_service)]


@router.get("/site", response_model=PublicResponse[SiteDTO])
async def site(service: Service) -> PublicResponse[SiteDTO]:
    return PublicResponse(data=await service.site())


@router.get("/contents", response_model=PublicResponse[Page[ContentSummary]])
async def contents(
    service: Service,
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    q: Annotated[str, Query(max_length=200)] = "",
    kind: ContentKind | None = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=200)] = None,
) -> PublicResponse[Page[ContentSummary]]:
    if page_size > runtime_from_request(request).settings.http.max_page_size:
        raise HTTPException(status_code=422)
    query = ContentQuery(page, page_size, q, kind, category, tag)
    return PublicResponse(data=await service.list_contents(query))


@router.get("/contents/resolve", response_model=PublicResponse[ContentDetail])
async def resolve(
    service: Service, path: Annotated[str, Query(min_length=1, max_length=2048, pattern="^/")]
) -> PublicResponse[ContentDetail]:
    return PublicResponse(data=await service.resolve(path))


@router.get("/contents/{content_id}/comments", response_model=PublicResponse[Page[PublicComment]])
async def comments(
    service: Service,
    request: Request,
    content_id: UUID,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PublicResponse[Page[PublicComment]]:
    if page_size > runtime_from_request(request).settings.http.max_page_size:
        raise HTTPException(status_code=422)
    return PublicResponse(data=await service.comments(content_id, page=page, page_size=page_size))


@router.get("/taxonomies", response_model=PublicResponse[TaxonomyList])
async def taxonomies(
    service: Service, kind: TaxonomyKind | None = None
) -> PublicResponse[TaxonomyList]:
    return PublicResponse(data=TaxonomyList(items=await service.taxonomies(kind)))
