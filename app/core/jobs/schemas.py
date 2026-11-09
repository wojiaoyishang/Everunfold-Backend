"""Private runtime management contract; no job payloads or connection secrets."""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.core.schemas import PublicModel


class RuntimeInstance(PublicModel):
    id: str
    role: str
    host: str
    pid: int
    started_at: float
    heartbeat: float
    state: str
    error: str | None = None
    last_scan: str | None = None
    residents: int = 0
    pending: int = 0
    queue: str | None = None


class RuntimePlan(PublicModel):
    restarts: int = 0
    id: str
    title: str
    handler: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    parameter_defaults: dict[str, Any] = Field(default_factory=dict)
    plugin: str
    kind: str
    enabled: bool
    available: bool
    interval: int
    manual: bool
    next_at: datetime | None


class RuntimeRun(PublicModel):
    id: str
    task_id: str
    status: str
    planned_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    connection: str | None = None
    last_message_at: str | None = None


class RuntimeOverview(PublicModel):
    queue_depth: int | None = None
    instances: list[RuntimeInstance]
    plans: list[RuntimePlan]
    runs: list[RuntimeRun]
    sampled_at: datetime
    telemetry_available: bool


class RuntimeCommand(PublicModel):
    task_id: str = Field(min_length=1, max_length=128)
    action: Literal["enable", "disable", "run", "debug_run"]
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_parameters(self) -> "RuntimeCommand":
        from app.core.jobs.parameters import checked_json

        checked_json(self.parameters)
        if self.action != "debug_run" and self.parameters:
            raise ValueError("Parameters are only supported for developer triggers")
        return self
