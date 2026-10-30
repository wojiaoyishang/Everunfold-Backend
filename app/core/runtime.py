"""Lifespan-owned connections, separate from request-scoped sessions."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.database import Database


@dataclass(slots=True)
class Runtime:
    settings: Settings
    database: Database
    redis: Redis

    @classmethod
    def create(cls, settings: Settings) -> Runtime:
        timeout = settings.runtime.health_check_timeout_seconds
        return cls(
            settings=settings,
            database=Database(settings.database.url.get_secret_value(), timeout_seconds=timeout),
            redis=Redis.from_url(
                settings.redis.url.get_secret_value(),
                socket_connect_timeout=timeout,
                socket_timeout=timeout,
                protocol=2,
            ),
        )

    async def close(self) -> None:
        await self.redis.aclose()
        await self.database.close()
