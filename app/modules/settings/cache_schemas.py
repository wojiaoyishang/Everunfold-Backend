"""Cache maintenance contracts for the administrator and the trusted node receiver."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.core.schemas import PublicModel
from app.modules.settings.cache_event_schema import CacheResource
from app.modules.settings.node_registry import NodeId
from app.modules.settings.nodes_schemas import DeliverySummary, ReceiverModel, SyncCheck

ByteCount = Annotated[int, Field(ge=0, le=9007199254740991)]
Revision = Annotated[int, Field(ge=1, le=9007199254740991)]


class CacheDiskResource(ReceiverModel):
    resource: CacheResource
    status: Literal["ready", "missing", "unavailable"]
    revision: Revision | None
    files: ByteCount | None
    bytes: ByteCount | None
    reclaimable_bytes: ByteCount | None

    @model_validator(mode="after")
    def coherent_replica(self) -> "CacheDiskResource":
        measured = (self.files, self.bytes, self.reclaimable_bytes)
        if (self.status == "ready") != (self.revision is not None):
            raise ValueError("Only a ready replica reports a revision")
        if any(value is None for value in measured) and (
            any(value is not None for value in measured) or self.status != "unavailable"
        ):
            raise ValueError("A disk measurement is either complete or unavailable")
        if self.reclaimable_bytes is not None and (
            self.bytes is None
            or self.reclaimable_bytes > self.bytes
            or (self.reclaimable_bytes and self.status != "ready")
        ):
            raise ValueError("Reclaimable bytes must be part of a verified replica's usage")
        return self


class CacheReceiverStatus(ReceiverModel):
    protocol: Literal["cache-status-1"]
    node_id: NodeId
    source_epoch: str
    resources: list[CacheDiskResource] = Field(min_length=3, max_length=3)
    free_bytes: ByteCount | None
    sync_check: SyncCheck | None = None

    @model_validator(mode="after")
    def unique_resources(self) -> "CacheReceiverStatus":
        if len({item.resource for item in self.resources}) != len(self.resources):
            raise ValueError("Each replica must appear exactly once")
        return self


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


class CacheDeletionReceipt(CacheReceiverTarget):
    removed_files: int = Field(ge=0)
    removed_bytes: ByteCount


class CachePruneReceipt(CacheDeletionReceipt):
    protocol: Literal["cache-prune-1"]


class CacheClearReceipt(CacheDeletionReceipt):
    protocol: Literal["cache-clear-1"]
