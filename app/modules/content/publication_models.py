"""Immutable complete public releases; private working drafts never enter this table."""

from typing import Any
from uuid import UUID

from sqlalchemy import Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ContentRelease(Base):
    __tablename__ = "content_releases"
    source_epoch: Mapped[UUID] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
