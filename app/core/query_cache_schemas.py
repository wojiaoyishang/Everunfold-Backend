"""Aggregate diagnostics for the instrumented public query paths."""

from datetime import datetime
from typing import Literal

from pydantic import field_validator

from app.core.schemas import PublicModel

QueryBusiness = Literal["site", "taxonomies", "content_list"]
QueryOutcome = Literal["hit", "miss", "bypass", "error"]
QueryCacheStatus = Literal["disabled", "unconfigured", "ready", "unavailable"]
QueryReportHours = Literal[1, 6, 24]
QueryMetricsStatus = Literal["ready", "unavailable"]


class QueryCacheReportRequest(PublicModel):
    hours: QueryReportHours = 6

    @field_validator("hours", mode="before")
    @classmethod
    def parse_http_hours(cls, value: object) -> object:
        if isinstance(value, str) and value in ("1", "6", "24"):
            return int(value)
        return value


class QueryCacheStats(PublicModel):
    queries: int
    hits: int
    misses: int
    bypasses: int
    errors: int
    sql_queries: int
    mean_ms: float | None
    p95_ms: float | None
    hit_rate: float | None


class QueryCachePoint(QueryCacheStats):
    at: datetime


class QueryCacheBusinessStats(QueryCacheStats):
    business: QueryBusiness
    trend: list[QueryCachePoint]


class QueryCacheReport(PublicModel):
    cache_status: QueryCacheStatus
    metrics_status: QueryMetricsStatus
    hours: QueryReportHours
    bucket_seconds: int
    checked_at: datetime
    total: QueryCacheStats
    businesses: list[QueryCacheBusinessStats]
    trend: list[QueryCachePoint]
