"""Versioned opt-in demo contract, separate from the public content snapshot."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.content.schemas import PublicModel, PublicResponse

Persona = Literal["guest", "member"]


class ReaderIdentityQuery(PublicModel):
    persona: Persona = "guest"


class ReaderMetricsQuery(PublicModel):
    ids: list[UUID] = Field(min_length=1, max_length=20)


class ReaderIdentity(PublicModel):
    mode: Literal["demo"]
    persona: Persona
    display_name: str
    sampled_at: datetime


class ReaderMetric(PublicModel):
    content_id: UUID
    views: int = Field(ge=0, le=996)


class ReaderMetrics(PublicModel):
    mode: Literal["demo"]
    sampled_at: datetime
    items: list[ReaderMetric]


class ArticleViewInput(PublicModel):
    content_id: UUID


class ArticleViewMetric(PublicModel):
    content_id: UUID
    views: int = Field(ge=0, le=(1 << 53) - 1)
    historical_likes: int | None = Field(
        default=None, ge=0, le=(1 << 53) - 1, exclude_if=lambda value: value is None
    )


class ArticleMetrics(PublicModel):
    items: list[ArticleViewMetric]


class ReaderPreviewContract(PublicModel):
    identity_query: ReaderIdentityQuery
    metrics_query: ReaderMetricsQuery
    identity: PublicResponse[ReaderIdentity]
    metrics: PublicResponse[ReaderMetrics]
    article_metrics: PublicResponse[ArticleMetrics]
    article_view_input: ArticleViewInput
    article_view: PublicResponse[ArticleViewMetric]
