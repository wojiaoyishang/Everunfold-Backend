"""Private SMTP configuration: secrets are write-only and never part of public settings."""

import ipaddress
import re
from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from app.core.schemas import PublicModel


class MailPreferences(PublicModel):
    enabled: bool = False
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    encryption: str = Field(default="starttls", pattern="^(starttls|tls|none)$")
    authenticate: bool = True
    username: str = Field(default="", max_length=320)
    from_address: str = Field(default="", max_length=254)
    from_name: str = Field(default="", max_length=200)
    reply_to: str = Field(default="", max_length=254)
    timeout_seconds: int = Field(default=15, ge=3, le=120)

    @field_validator("host", "username", "from_address", "from_name", "reply_to")
    @classmethod
    def single_line(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Must be a single line without control characters")
        return value.strip()

    @field_validator("host")
    @classmethod
    def hostname(cls, value: str) -> str:
        if not value:
            return value
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            pass
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Invalid SMTP hostname") from exc
        if not all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
            for label in value.split(".")
        ):
            raise ValueError("Enter a hostname or IP without scheme, credentials or port")
        return value.lower()

    @field_validator("from_address", "reply_to")
    @classmethod
    def email(cls, value: str) -> str:
        if value and not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value
        ):
            raise ValueError("Enter a single email address without a display name")
        return value


class MailSenderProfileInput(PublicModel):
    id: UUID
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    is_default: bool = False
    value: MailPreferences
    password: SecretStr | None = Field(default=None, max_length=2048)
    clear_password: bool = False

    @field_validator("name")
    @classmethod
    def profile_name(cls, value: str) -> str:
        value = MailPreferences.single_line(value).strip()
        if not value:
            raise ValueError("Mail profile name cannot be empty")
        return value

    @model_validator(mode="after")
    def password_action(self) -> Self:
        if self.clear_password and self.password and self.password.get_secret_value():
            raise ValueError("Cannot replace and clear password together")
        return self


class MailSenderProfileView(PublicModel):
    id: UUID
    name: str
    enabled: bool
    is_default: bool
    value: MailPreferences
    password_configured: bool


class MailSettingsState(PublicModel):
    revision: int
    profiles: list[MailSenderProfileView]


class SaveMailSettings(PublicModel):
    expected_revision: int = Field(ge=0)
    profiles: list[MailSenderProfileInput] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_profiles(self) -> Self:
        ids = [item.id for item in self.profiles]
        names = [item.name.casefold() for item in self.profiles]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate mail profile id")
        if len(set(names)) != len(names):
            raise ValueError("duplicate mail profile name")
        defaults = [item for item in self.profiles if item.is_default]
        if self.profiles and len(defaults) != 1:
            raise ValueError("exactly one mail profile must be the default")
        return self


class SendTestMail(PublicModel):
    request_id: UUID
    expected_revision: int = Field(ge=0)
    profile_id: UUID
    recipient: str = Field(min_length=3, max_length=254)

    @field_validator("recipient")
    @classmethod
    def address(cls, value: str) -> str:
        return MailPreferences.email(MailPreferences.single_line(value))


class MailTestLookup(PublicModel):
    id: UUID


class MailTestState(PublicModel):
    id: UUID
    profile_id: UUID | None = None
    recipient: str
    status: Literal["queued", "sending", "sent", "failed", "unknown"]
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
