"""Plans, execution ownership and final results survive Redis restarts."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaskPlan(Base):
    __tablename__ = "runtime_task_plans"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool]
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class TaskRun(Base):
    __tablename__ = "runtime_task_runs"
    __table_args__ = (UniqueConstraint("task_id", "planned_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(String(80))
