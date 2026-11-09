"""Durable, ordered and bounded progress events for task resources."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaskProgressEventRecord(Base):
    __tablename__ = "task_progress_events"
    __table_args__ = (
        Index("ix_task_progress_resource_sequence", "resource", "resource_id", "sequence"),
    )
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    resource: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[UUID]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(8192))
    stage: Mapped[str] = mapped_column(String(80))
    completed: Mapped[float | None]
    total: Mapped[float | None]
    unit: Mapped[str | None] = mapped_column(String(40))
