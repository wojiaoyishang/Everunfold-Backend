"""Optional cache for versioned public query results."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.query_cache_schemas import QueryBusiness, QueryCacheStatus, QueryOutcome
from app.core.redis_keys import redis_key

type Variant = tuple[str | int | bool | None, ...]


@dataclass(frozen=True, slots=True)
class QueryRevision:
    source: str
    settings: int
    content: int
    checked_at: datetime = field(compare=False)

    def ttl(self, maximum: int, deadline: datetime | None) -> int:
        if deadline is None:
            return maximum
        return min(
            maximum, int((deadline - max(self.checked_at, datetime.now(UTC))).total_seconds())
        )


class QueryCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = settings.query_cache
        self._timeout = self.config.timeout_ms / 1000
        self._backoff_until = 0.0
        self.client: Redis | None = None
        if self.config.enabled and self.config.url is not None:
            self.client = Redis.from_url(
                self.config.url.get_secret_value(),
                socket_connect_timeout=self._timeout,
                socket_timeout=self._timeout,
                retry=Retry(NoBackoff(), 0),
                max_connections=32,
                protocol=2,
            )

    @property
    def unavailable(self) -> bool:
        return self.client is not None and monotonic() < self._backoff_until

    async def status(self) -> QueryCacheStatus:
        if not self.config.enabled:
            return "disabled"
        if self.client is None:
            return "unconfigured"
        if self.unavailable:
            return "unavailable"
        try:
            async with asyncio.timeout(self._timeout):
                return "ready" if await self.client.ping() else "unavailable"
        except (RedisError, TimeoutError):
            return "unavailable"

    def _key(self, resource: QueryBusiness, revision: QueryRevision, variant: Variant) -> str:
        digest = hashlib.sha256(json.dumps(variant, separators=(",", ":")).encode()).hexdigest()
        return redis_key(
            self.settings,
            "query-cache-v1",
            revision.source,
            str(revision.settings),
            str(revision.content),
            resource,
            digest,
        )

    async def read[T: BaseModel](
        self,
        resource: QueryBusiness,
        model: type[T],
        revision: QueryRevision | None,
        variant: Variant,
        load: Callable[[], Awaitable[T]],
        latest_revision: Callable[[], Awaitable[QueryRevision | None]],
        opening_deadline: Callable[[], Awaitable[datetime | None]],
    ) -> tuple[T, QueryOutcome]:
        client = self.client
        if client is None or revision is None:
            return await load(), "bypass"
        if self.unavailable:
            return await load(), "error"
        key = self._key(resource, revision, variant)
        outcome: QueryOutcome = "miss"
        try:
            async with asyncio.timeout(self._timeout):
                raw = await client.get(key)
            if raw is not None:
                if len(raw) > self.config.max_value_bytes:
                    raise ValueError("Oversized cached value")
                deadline_raw, separator, payload = raw.partition(b"\n")
                if not separator:
                    raise ValueError("Cached value is missing its deadline field")
                deadline = datetime.fromisoformat(deadline_raw.decode()) if deadline_raw else None
                if deadline is not None and deadline.utcoffset() is None:
                    raise ValueError("Cached deadline is missing its time zone")
                if revision.ttl(self.config.ttl_seconds, deadline) > 0:
                    return model.model_validate_json(payload), "hit"
        except (RedisError, TimeoutError):
            self._backoff_until = monotonic() + 1
            return await load(), "error"
        except (ValidationError, ValueError):
            outcome = "error"

        # PostgreSQL evaluates opening rules against the start of this read transaction.
        deadline = await opening_deadline()
        value = await load()
        encoded = (deadline.isoformat().encode() if deadline else b"") + b"\n"
        encoded += value.model_dump_json().encode()
        if len(encoded) > self.config.max_value_bytes:
            return value, "bypass"
        # Avoid populating a version if a commit landed during the database reads.
        current = await latest_revision()
        if current != revision:
            return value, outcome
        ttl = current.ttl(self.config.ttl_seconds, deadline)
        if ttl <= 0:
            return value, "bypass"
        try:
            async with asyncio.timeout(self._timeout):
                await client.set(key, encoded, ex=ttl)
        except (RedisError, TimeoutError):
            self._backoff_until = monotonic() + 1
            outcome = "error"
        return value, outcome

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
