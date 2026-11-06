"""Private per-user avatar library and administrator-controlled upload limits."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AvatarPolicy(Base):
    __tablename__ = "account_avatar_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    max_bytes: Mapped[int] = mapped_column(Integer, default=2097152, server_default="2097152")
    max_edge: Mapped[int] = mapped_column(Integer, default=512, server_default="512")
    daily_uploads: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    retained_images: Mapped[int] = mapped_column(Integer, default=20, server_default="20")


class UserAvatar(Base):
    __tablename__ = "account_avatars"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("media_assets.id"))
    filename: Mapped[str] = mapped_column(String(160))
    signature: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
