"""Private SMTP configuration: secrets are write-only and never part of public settings."""

import ipaddress
import re
from typing import Self

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


class MailSettingsState(PublicModel):
    revision: int
    value: MailPreferences
    password_configured: bool


class SaveMailSettings(PublicModel):
    expected_revision: int = Field(ge=0)
    value: MailPreferences
    password: SecretStr | None = Field(default=None, max_length=2048)
    clear_password: bool = False

    @model_validator(mode="after")
    def password_action(self) -> Self:
        if self.clear_password and self.password and self.password.get_secret_value():
            raise ValueError("Cannot replace and clear password together")
        return self
