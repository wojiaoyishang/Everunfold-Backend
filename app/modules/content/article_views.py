"""Count visits to addressable article pages without inspecting protected bodies."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import AuthRepository
from app.modules.auth.router import peer
from app.modules.auth.service import AuthFailure
from app.modules.comments.workspace_models import ArticleViewDay
from app.modules.content.models import Content
from app.modules.content.reader_preview_schema import (
    ArticleMetrics,
    ArticleViewInput,
    ArticleViewMetric,
    ReaderMetricsQuery,
)
from app.modules.content.repository import presentation_conditions
from app.modules.content.router import get_session, runtime_from_request
from app.modules.content.schemas import PublicResponse
from app.modules.settings.browser_origins import managed_origin

router = APIRouter(prefix="/v1/reader", tags=["article-views"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/metrics", response_model=PublicResponse[ArticleMetrics])
async def article_metrics(
    query: Annotated[ReaderMetricsQuery, Query()], session: Session, response: Response
) -> PublicResponse[ArticleMetrics]:
    response.headers["Cache-Control"] = "no-store"
    rows = await session.execute(
        select(Content.id, Content.view_count).where(
            *presentation_conditions(), Content.kind == "article", Content.id.in_(query.ids)
        )
    )
    return PublicResponse(
        data=ArticleMetrics(
            items=[ArticleViewMetric(content_id=id_, views=views) for id_, views in rows]
        )
    )


@router.post("/views", response_model=PublicResponse[ArticleViewMetric])
async def record_view(
    payload: ArticleViewInput, request: Request, response: Response, session: Session
) -> PublicResponse[ArticleViewMetric]:
    response.headers["Cache-Control"] = "no-store"
    origin = request.headers.get("origin")
    config = runtime_from_request(request).settings
    if not origin or (
        origin not in config.cors.allowed_origins and origin != managed_origin(request.scope)
    ):
        raise AuthFailure(403, 40350, "请求来源不被允许")
    if not await AuthRepository(session).consume_limit(
        f"article-views:{peer(request)}", 600, 60, datetime.now(UTC)
    ):
        raise AuthFailure(429, 42950, "请求较多，请稍后重试")
    views = await session.scalar(
        update(Content)
        .where(
            Content.id == payload.content_id, Content.kind == "article", *presentation_conditions()
        )
        .values(view_count=func.least(Content.view_count + 1, (1 << 53) - 1))
        .returning(Content.view_count)
    )
    if views is not None:
        await session.execute(
            insert(ArticleViewDay)
            .values(content_id=payload.content_id, day=datetime.now(UTC).date(), views=1)
            .on_conflict_do_update(
                index_elements=[ArticleViewDay.content_id, ArticleViewDay.day],
                set_={"views": func.least(ArticleViewDay.views + 1, (1 << 53) - 1)},
            )
        )
    await session.commit()
    if views is None:
        raise AuthFailure(404, 40450, "内容不存在")
    return PublicResponse(data=ArticleViewMetric(content_id=payload.content_id, views=views))
