"""The versioned browser authentication contract; no credentials in identity output."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.schemas import ErrorResponse, PublicModel, PublicResponse
from app.modules.captcha.schemas import CaptchaAnswer


class LoginInput(PublicModel):
    captcha: CaptchaAnswer | None = None
    username: str = Field(min_length=1, max_length=64)
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
    user: AuthUser | None
    csrf_token: str
    expires_at: datetime


class AuthContract(PublicModel):
    login: LoginInput
    session: PublicResponse[AuthSessionState]
    error: ErrorResponse
