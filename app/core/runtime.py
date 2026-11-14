"""Lifespan-owned connections, separate from request-scoped sessions."""

from __future__ import annotations

from dataclasses import dataclass

from anyio import CapacityLimiter
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import Database
from app.core.query_cache import QueryCache
from app.core.query_cache_metrics import QueryCacheMetrics


@dataclass(slots=True)
class Runtime:
    settings: Settings
    database: Database
    redis: Redis
    query_cache: QueryCache
    query_cache_metrics: QueryCacheMetrics
    password_limiter: CapacityLimiter

    @classmethod
    def create(cls, settings: Settings) -> Runtime:
        timeout = settings.runtime.health_check_timeout_seconds
        redis = Redis.from_url(
            settings.redis.url.get_secret_value(),
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            protocol=2,
        )
        return cls(
            settings=settings,
            database=Database(settings.database.url.get_secret_value(), timeout_seconds=timeout),
            redis=redis,
            query_cache=QueryCache(settings),
            query_cache_metrics=QueryCacheMetrics(redis, settings.redis_prefix),
            password_limiter=CapacityLimiter(2),
        )

    async def close(self) -> None:
        await self.query_cache_metrics.close()
        await self.query_cache.close()
        await self.redis.aclose()
        await self.database.close()
