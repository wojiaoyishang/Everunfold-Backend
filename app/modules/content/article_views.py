"""Count visits to addressable articles and public moments."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.auth.repository import AuthRepository
from app.modules.auth.router import peer
from app.modules.auth.service import AuthFailure
from app.modules.comments.workspace_models import ArticleViewDay
from app.modules.content.models import Content, SiteSettings
from app.modules.content.reader_preview_schema import (
    ArticleMetrics,
    ArticleViewInput,
    ArticleViewMetric,
    ReaderMetricsQuery,
)
from app.modules.content.repository import presentation_conditions, public_conditions
from app.modules.content.router import get_session, runtime_from_request
from app.modules.content.schemas import PublicResponse
from app.modules.settings.browser_origins import managed_origin

router = APIRouter(prefix="/v1/reader", tags=["article-views"])
Session = Annotated[AsyncSession, Depends(get_session)]


def visible_metrics() -> tuple[ColumnElement[bool], ...]:
    return (
        *presentation_conditions(),
        or_(Content.kind == "article", (Content.kind == "shuoshuo") & public_conditions()[1]),
    )


@router.get("/metrics", response_model=PublicResponse[ArticleMetrics])
async def article_metrics(
    query: Annotated[ReaderMetricsQuery, Query()], session: Session, response: Response
) -> PublicResponse[ArticleMetrics]:
    response.headers["Cache-Control"] = "no-store"
    rows = (
        await session.execute(
            select(Content.id, Content.kind, Content.view_count).where(
                *visible_metrics(), Content.id.in_(query.ids)
            )
        )
    ).all()
    historical: dict[str, dict[str, int]] = {}
    if any(kind == "shuoshuo" for _, kind, _ in rows):
        historical = (
            await session.scalar(
                select(SiteSettings.data["_wordpress_engagement"]).where(SiteSettings.id == 1)
            )
            or {}
        )
    return PublicResponse(
        data=ArticleMetrics(
            items=[
                ArticleViewMetric(
                    content_id=id_,
                    views=views,
                    historical_likes=historical.get(str(id_), {}).get("likes")
                    if kind == "shuoshuo"
                    else None,
                )
                for id_, kind, views in rows
            ]
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
    row = (
        await session.execute(
            update(Content)
            .where(Content.id == payload.content_id, *visible_metrics())
            .values(view_count=func.least(Content.view_count + 1, (1 << 53) - 1))
            .returning(Content.kind, Content.view_count)
        )
    ).one_or_none()
    if row is None:
        raise AuthFailure(404, 40450, "内容不存在")
    kind, views = row
    if kind == "article":
        await session.execute(
            insert(ArticleViewDay)
            .values(content_id=payload.content_id, day=datetime.now(UTC).date(), views=1)
            .on_conflict_do_update(
                index_elements=[ArticleViewDay.content_id, ArticleViewDay.day],
                set_={"views": func.least(ArticleViewDay.views + 1, (1 << 53) - 1)},
            )
        )
    await session.commit()
    return PublicResponse(data=ArticleViewMetric(content_id=payload.content_id, views=views))
