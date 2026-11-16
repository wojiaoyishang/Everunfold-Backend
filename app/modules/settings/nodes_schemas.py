"""Shared monitoring payloads for deployment and administrator consoles."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from app.core.schemas import PublicModel
from app.modules.settings.node_registry import NodeId, NodeRegistration

Resource = Literal["public-settings", "cache-policy", "public-content"]


class ReceiverModel(PublicModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReadyReplica(ReceiverModel):
    status: Literal["ready"]
    revision: int = Field(ge=1, le=9007199254740991)


class UnavailableReplica(ReceiverModel):
    status: Literal["missing", "unavailable"]
    revision: None


ReceiverReplica = ReadyReplica | UnavailableReplica


class SyncCheck(ReceiverModel):
    interval_seconds: int = Field(ge=1, le=86400)
    last_checked_at: AwareDatetime | None
    next_check_at: AwareDatetime | None
    state: Literal["idle", "ready", "queued", "waiting", "error"]


class ReceiverStatus(ReceiverModel):
    protocol: Literal["node-status-1"]
    node_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    source_epoch: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    public_settings: ReceiverReplica
    cache_policy: ReceiverReplica
    public_content: ReceiverReplica
    sync_check: SyncCheck | None = None


class DeliverySummary(PublicModel):
    expected: int
    acknowledged: int | None = None
    acknowledged_at: datetime | None = None
    state: Literal[
        "disabled", "not_configured", "unqueued", "pending", "retrying", "failed", "applied"
    ]
    attempts: int = 0
    attempt_limit: int
    next_retry_at: datetime | None = None
    error: str | None = None


class ReceiverProbe(PublicModel):
    state: Literal[
        "unavailable",
        "disabled",
        "not_configured",
        "credential_missing",
        "unreachable",
        "rejected",
        "unsupported",
        "invalid",
        "wrong_node",
        "wrong_source",
        "reachable",
    ]
    checked_at: datetime | None = None
    replicas: dict[Resource, ReceiverReplica] = Field(default_factory=dict)
    sync_check: SyncCheck | None = None


class NodeStatus(PublicModel):
    id: str
    enabled: bool
    base_url: str
    bound: bool = False
    receiver_configured: bool = False
    receiver: ReceiverProbe
    last_report: datetime | None = None
    inbound: Literal["recent", "stale", "never", "unavailable"] = "unavailable"
    sync: dict[Resource, DeliverySummary] = Field(default_factory=dict)


class MonitorStatus(PublicModel):
    sampled_at: datetime
    api: Literal["online"] = "online"
    database: Literal["online", "unavailable"]
    redis: Literal["online", "unavailable"]
    worker: str
    scheduler: str
    history_available: bool
    registry_revision: int | None = None
    nodes: list[NodeStatus]


class NodeChange(PublicModel):
    action: Literal["add", "edit", "delete", "sync"]
    node_id: NodeId
    expected_revision: Annotated[int, Field(ge=0, strict=True)]
    registration: NodeRegistration | None = None

    @model_validator(mode="after")
    def require_registration(self) -> "NodeChange":
        if (self.action in {"add", "edit"}) != (self.registration is not None):
            raise ValueError("unexpected node registration")
        return self


class NodeChangeResult(PublicModel):
    revision: int
    queued: bool


class NodeProofRequest(PublicModel):
    node_id: NodeId
    intent: Literal["inspect", "switch"]
    profile: NodeId
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenge: str = Field(pattern=r"^[0-9a-f]{32}$")


class NodeProof(PublicModel):
    expires: int
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
