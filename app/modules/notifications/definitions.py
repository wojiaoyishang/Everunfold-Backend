"""Event metadata shared by the built-in catalog and trusted plugin setup."""

from dataclasses import dataclass
from typing import Literal

RecipientMode = Literal["configurable", "subject", "admins"]
DeliveryMode = Literal["notification", "security_required", "security_optional"]


@dataclass(frozen=True)
class NotificationEventDefinition:
    id: str
    title: str
    description: str
    variables: tuple[str, ...] = ()
    requires_code: bool = False
    recipient_mode: RecipientMode = "configurable"
    delivery_mode: DeliveryMode = "notification"
    email_only: bool = False

    @property
    def fixed_recipient(self) -> tuple[str, str] | None:
        if self.recipient_mode == "subject":
            return "subject", "subject"
        if self.recipient_mode == "admins":
            return "role", "admin"
        return None

    def accepts_recipient(self, kind: str, value: str) -> bool:
        if self.fixed_recipient is not None:
            return (kind, value) == self.fixed_recipient
        return kind in {"user", "role", "permission"}

    def accepts_platform(self, platform: str) -> bool:
        return not self.email_only or platform == "email"
