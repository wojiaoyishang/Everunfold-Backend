"""A short-lived password recovery challenge and its security mail delivery."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PasswordRecovery(Base):
    __tablename__ = "password_recoveries"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_revision: Mapped[int]
    auth_version: Mapped[int]
    policy_epoch: Mapped[int]
    email: Mapped[str] = mapped_column(String(254))
    mail_profile_id: Mapped[str] = mapped_column(String(36))
    mail_revision: Mapped[int]
    code_hash: Mapped[str] = mapped_column(String(64))
    delivery_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempts: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
