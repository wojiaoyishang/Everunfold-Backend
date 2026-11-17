"""Private cache maintenance and per-content receipt status."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from app.core.schemas import PublicModel
from app.modules.content.schemas import ContentKind

type ContentCacheStatus = Literal[
    "disabled",
    "not_configured",
    "not_published",
    "unqueued",
    "pending",
    "retrying",
    "failed",
    "applied",
]


class ContentCacheTarget(PublicModel):
    id: UUID
    kind: ContentKind


class ContentCacheCommand(ContentCacheTarget):
    action: Literal["clear", "resync"]


class ContentCacheNode(PublicModel):
    node_id: str
    state: ContentCacheStatus
    acknowledged_revision: int | None = None
    acknowledged_at: datetime | None = None
    error: str | None = None


class ContentCacheState(PublicModel):
    published: bool
    manageable: bool
    nodes: list[ContentCacheNode]
