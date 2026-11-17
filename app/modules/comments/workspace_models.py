"""Private workspace preferences and anonymous daily article totals."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WorkspaceLayout(Base):
    __tablename__ = "workspace_layouts"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cards: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)


class ArticleViewDay(Base):
    __tablename__ = "article_view_days"
    __table_args__ = (CheckConstraint("views >= 0", name="nonnegative_views"),)
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    views: Mapped[int] = mapped_column(BigInteger, nullable=False)


class WorkspaceAnalyticsEpoch(Base):
    __tablename__ = "workspace_analytics_epoch"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
