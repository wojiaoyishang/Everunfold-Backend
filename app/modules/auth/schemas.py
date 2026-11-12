"""The versioned browser authentication contract; no credentials in identity output."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import ErrorResponse, PublicModel, PublicResponse
from app.modules.auth.registration_schemas import (
    RegistrationConfirm,
    RegistrationOptions,
    RegistrationRequest,
    RegistrationState,
)
from app.modules.auth.two_factor_schemas import (
    TwoFactorActivated,
    TwoFactorBegin,
    TwoFactorChange,
    TwoFactorCode,
    TwoFactorSetup,
    TwoFactorState,
)
from app.modules.captcha.schemas import CaptchaAnswer


class LoginInput(PublicModel):
    captcha: CaptchaAnswer | None = None
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class AuthUser(PublicModel):
    id: UUID
    username: str
    display_name: str
    role: Literal["admin", "user"]
    permissions: dict[str, Literal["own", "all"]] = Field(default_factory=dict)
    avatar_url: str = ""
    preferred_locale: str = "zh-CN"
    must_change_password: bool = False


class AuthSessionState(PublicModel):
    authenticated: bool
    mfa_required: bool = False
    user: AuthUser | None
    csrf_token: str
    expires_at: datetime


class AuthContract(PublicModel):
    registration_options: PublicResponse[RegistrationOptions]
    registration_request: RegistrationRequest
    registration_confirm: RegistrationConfirm
    registration_state: PublicResponse[RegistrationState]
    login: LoginInput
    second_factor: TwoFactorCode
    session: PublicResponse[AuthSessionState]
    two_factor_begin: TwoFactorBegin
    two_factor_change: TwoFactorChange
    two_factor_setup: PublicResponse[TwoFactorSetup]
    two_factor_state: PublicResponse[TwoFactorState]
    two_factor_activated: PublicResponse[TwoFactorActivated]
    error: ErrorResponse
