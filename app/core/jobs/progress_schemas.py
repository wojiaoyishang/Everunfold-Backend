"""One progress vocabulary for task-producing features and frontend monitoring."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import PublicModel

Resource = Literal["runtime", "mail-test"]
Level = Literal["info", "warning", "error"]
Kind = Literal["stage", "request", "response", "message"]
State = Literal["queued", "running", "succeeded", "failed", "unknown", "cancelled"]


class WatchTask(PublicModel):
    resource: Resource
    id: UUID
    after_sequence: int = Field(default=0, ge=0)
    wait_seconds: int = Field(default=15, ge=0, le=20)


class TaskProgressEvent(PublicModel):
    sequence: int
    at: datetime
    level: Level
    kind: Kind
    message: str


class TaskProgress(PublicModel):
    resource: Resource
    id: UUID
    state: State
    stage: str
    completed: float | None
    total: float | None
    unit: str | None
    percent: float | None
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cursor: int
    has_more: bool
    events: list[TaskProgressEvent]
