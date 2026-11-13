"""Versioned metadata delivered to trusted cache plugins after a commit."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict

from app.core.schemas import PublicModel

CacheResource = Literal["public-content", "public-settings", "cache-policy"]
CacheReason = Literal[
    "content_published",
    "content_updated",
    "content_withdrawn",
    "content_opened",
    "projection_changed",
    "page_changed",
    "translations_changed",
    "taxonomy_changed",
    "comments_changed",
    "settings_changed",
    "policy_changed",
]


@dataclass(frozen=True)
class CacheChange:
    reason: CacheReason
    content_id: UUID | None = None
    paths: tuple[str, ...] = ()


class CacheEvent(PublicModel):
    model_config = ConfigDict(frozen=True)

    protocol: Literal["cache-event-1"] = "cache-event-1"
    id: UUID
    source_epoch: UUID
    resource: CacheResource
    revision: int
    occurred_at: datetime
    reason: CacheReason
    node_id: str | None
    content_id: UUID | None
    paths: tuple[str, ...]


CacheEventHandler = Callable[[CacheEvent], Awaitable[None]]
