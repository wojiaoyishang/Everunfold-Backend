"""Public signup and private invitation contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import PublicModel
from app.modules.captcha.schemas import CaptchaAnswer

RegistrationMode = Literal["closed", "invite", "open"]
RegistrationStatus = Literal["queued", "sending", "sent", "failed", "unknown", "expired"]


class RegistrationOptions(PublicModel):
    mode: RegistrationMode
    mail_ready: bool


class RegistrationRequest(PublicModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=1024, repr=False)
    invite_code: str | None = Field(default=None, max_length=100, repr=False)
    locale: Literal["zh-CN", "en"] = "zh-CN"
    captcha: CaptchaAnswer | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        import re

        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("invalid email")
        return value

    @field_validator("display_name")
    @classmethod
    def display_name_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display name required")
        return value.strip()


class RegistrationConfirm(PublicModel):
    request_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


class RegistrationState(PublicModel):
    request_id: UUID
    status: RegistrationStatus
    expires_at: datetime
    retry_after_seconds: int = Field(ge=0)


class InvitationCreate(PublicModel):
    days_valid: int = Field(default=7, ge=1, le=30)


class InvitationCreated(PublicModel):
    id: UUID
    code: str = Field(repr=False)
    expires_at: datetime


class InvitationItem(PublicModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None


class InvitationList(PublicModel):
    items: list[InvitationItem]


class InvitationRevoke(PublicModel):
    id: UUID
