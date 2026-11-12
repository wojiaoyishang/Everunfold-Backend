"""Public password recovery endpoints never disclose account or delivery status."""

from pydantic import Field, field_validator

from app.core.schemas import PublicModel


class RecoveryOptions(PublicModel):
    enabled: bool


class RecoveryRequest(PublicModel):
    identifier: str = Field(min_length=1, max_length=254)

    @field_validator("identifier")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("identifier is required")
        return value


class RecoveryConfirm(PublicModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$", repr=False)
    new_password: str = Field(min_length=12, max_length=1024, repr=False)


class RecoveryAccepted(PublicModel):
    accepted: bool = True
