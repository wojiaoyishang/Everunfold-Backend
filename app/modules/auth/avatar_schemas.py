"""Only safe owner-specific avatar metadata is exposed to ordinary users."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import PublicModel


class AvatarLimits(PublicModel):
    max_bytes: int = Field(default=2097152, ge=65536, le=20971520)
    max_edge: int = Field(default=512, ge=64, le=2048)
    daily_uploads: int = Field(default=10, ge=1, le=1000)
    retained_images: int = Field(default=20, ge=1, le=1000)


class AvatarPolicyState(AvatarLimits):
    revision: int


class AvatarPolicySave(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    value: AvatarLimits
    current_password: str = Field(min_length=1, max_length=1024, repr=False)


class AvatarItem(PublicModel):
    id: UUID
    filename: str
    url: str | None
    created_at: datetime
    status: Literal["ready", "pending", "unavailable"]
    reused: bool = False


class AvatarCatalog(PublicModel):
    items: list[AvatarItem]
    policy: AvatarPolicyState
    uploaded_today: int
    retained: int
    resets_at: datetime


class AvatarUpload(PublicModel):
    upload_id: UUID
    filename: str = Field(min_length=1, max_length=160)


class AvatarRemove(PublicModel):
    operation_id: UUID
    avatar_id: UUID
