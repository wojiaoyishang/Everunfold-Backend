"""Durable CRDT state, immutable update receipts and single-use socket tickets."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CollaborationDocument(Base):
    __tablename__ = "collaboration_documents"

    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), primary_key=True)
    epoch: Mapped[UUID] = mapped_column(unique=True)
    committed_seq: Mapped[int] = mapped_column(Integer)
    initial_state: Mapped[bytes] = mapped_column(LargeBinary)
    state: Mapped[bytes] = mapped_column(LargeBinary)
    media_ids: Mapped[list[str]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CollaborationUpdate(Base):
    __tablename__ = "collaboration_updates"
    __table_args__ = (UniqueConstraint("draft_id", "committed_seq"),)

    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("collaboration_documents.draft_id"), primary_key=True
    )
    update_id: Mapped[UUID] = mapped_column(primary_key=True)
    committed_seq: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CollaborationTicket(Base):
    __tablename__ = "collaboration_tickets"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
