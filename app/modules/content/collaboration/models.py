"""Durable CRDT state, immutable update receipts and single-use socket tickets."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
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
    initial_seq: Mapped[int] = mapped_column(Integer, default=0)
    source_first: Mapped[bool] = mapped_column(Boolean, default=False)


class CollaborationEpoch(Base):
    __tablename__ = "collaboration_epochs"

    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), primary_key=True)
    epoch: Mapped[UUID] = mapped_column(primary_key=True)
    initial_seq: Mapped[int] = mapped_column(Integer)
    final_seq: Mapped[int | None] = mapped_column(Integer)
    initial_state: Mapped[bytes] = mapped_column(LargeBinary)


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
    source_signature: Mapped[str | None] = mapped_column(String(64))


class SourceLease(Base):
    __tablename__ = "collaboration_source_leases"
    draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("collaboration_documents.draft_id"), primary_key=True
    )
    block_id: Mapped[UUID] = mapped_column(primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[UUID | None] = mapped_column()
    session_hash: Mapped[str | None] = mapped_column(String(64))
    client_id: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CollaborationTicket(Base):
    __tablename__ = "collaboration_tickets"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[UUID] = mapped_column(ForeignKey("content_drafts.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
