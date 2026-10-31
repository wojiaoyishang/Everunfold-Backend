"""Read-only synthetic reader endpoints. Neither endpoint grants access or records views."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.modules.content.reader_preview_schema import (
    ReaderIdentity,
    ReaderIdentityQuery,
    ReaderMetric,
    ReaderMetrics,
    ReaderMetricsQuery,
)
from app.modules.content.repository import ContentRepository
from app.modules.content.router import Service, runtime_from_request
from app.modules.content.router import router as public_router
from app.modules.content.schemas import PublicResponse


async def require_preview(request: Request, response: Response) -> None:
    if not runtime_from_request(request).settings.reader_preview.enabled:
        raise HTTPException(status_code=404, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(
    prefix="/v1/reader-preview",
    tags=["reader-preview"],
    dependencies=[Depends(require_preview)],
    responses=public_router.responses,
)


@router.get("/identity", response_model=PublicResponse[ReaderIdentity])
async def identity(
    query: Annotated[ReaderIdentityQuery, Query()],
) -> PublicResponse[ReaderIdentity]:
    return PublicResponse(
        data=ReaderIdentity(
            mode="demo",
            persona=query.persona,
            display_name="演示读者" if query.persona == "member" else "演示访客",
            sampled_at=datetime.now(UTC),
        )
    )


@router.get("/metrics", response_model=PublicResponse[ReaderMetrics])
async def metrics(
    query: Annotated[ReaderMetricsQuery, Query()], service: Service
) -> PublicResponse[ReaderMetrics]:
    # The same visibility filter as public reading; unknown/private IDs are indistinguishable.
    repository: ContentRepository = service.repository
    visible = set(await repository.public_ids(query.ids))
    return PublicResponse(
        data=ReaderMetrics(
            mode="demo",
            sampled_at=datetime.now(UTC),
            items=[
                ReaderMetric(content_id=item, views=item.int % 997)
                for item in dict.fromkeys(query.ids)
                if item in visible
            ],
        )
    )
