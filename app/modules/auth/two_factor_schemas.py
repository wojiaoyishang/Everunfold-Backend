"""Browser-only TOTP and recovery-code contracts."""

from datetime import datetime

from pydantic import Field

from app.core.schemas import PublicModel


class TwoFactorState(PublicModel):
    enabled: bool
    enabled_at: datetime | None
    recovery_codes_remaining: int
    available: bool


class TwoFactorBegin(PublicModel):
    current_password: str = Field(min_length=1, max_length=1024, repr=False)


class TwoFactorSetup(PublicModel):
    secret: str = Field(repr=False)
    otpauth_uri: str = Field(repr=False)
    expires_at: datetime


class TwoFactorCode(PublicModel):
    code: str = Field(min_length=6, max_length=32, repr=False)


class TwoFactorChange(PublicModel):
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    code: str = Field(min_length=6, max_length=32, repr=False)


class TwoFactorActivated(PublicModel):
    recovery_codes: list[str] = Field(repr=False)
