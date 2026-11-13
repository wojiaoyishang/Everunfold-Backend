"""Cache maintenance contracts for the administrator and the trusted node receiver."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.core.schemas import PublicModel
from app.modules.settings.cache_event_schema import CacheResource
from app.modules.settings.node_registry import NodeId
from app.modules.settings.nodes_schemas import DeliverySummary, ReceiverModel

ByteCount = Annotated[int, Field(ge=0, le=9007199254740991)]


class CacheDiskResource(ReceiverModel):
    resource: CacheResource
    status: Literal["ready", "missing", "unavailable"]
    revision: int | None
    files: int = Field(ge=0)
    bytes: ByteCount
    reclaimable_bytes: ByteCount


class CacheReceiverStatus(ReceiverModel):
    protocol: Literal["cache-status-1"]
    node_id: NodeId
    source_epoch: str
    resources: list[CacheDiskResource] = Field(max_length=3)
    free_bytes: ByteCount | None


class CacheNodeState(PublicModel):
    node_id: NodeId
    checked_at: datetime
    receiver: Literal[
        "ready",
        "disabled",
        "not_configured",
        "credential_missing",
        "unreachable",
        "rejected",
        "unsupported",
        "invalid",
        "identity_mismatch",
    ]
    storage: CacheReceiverStatus | None
    sync: dict[CacheResource, DeliverySummary]


class CacheRebuild(PublicModel):
    scope: Literal["all", "public-content", "public-settings", "cache-policy"]


class CacheRebuildResult(PublicModel):
    queued: bool


class CacheReceiverTarget(ReceiverModel):
    node_id: NodeId
    source_epoch: str


class CachePruneReceipt(CacheReceiverTarget):
    protocol: Literal["cache-prune-1"]
    removed_files: int = Field(ge=0)
    removed_bytes: ByteCount
