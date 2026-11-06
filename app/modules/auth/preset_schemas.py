"""Permission presets carry member grants, never an administrator role."""

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import PublicModel
from app.modules.auth.permissions import ResourceScope


class UserPresetSelection(PublicModel):
    id: UUID
    expected_revision: int = Field(ge=1)


class UserPresetStamp(PublicModel):
    id: UUID
    name: str
    revision: int


class UserPresetFields(PublicModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    grants: dict[str, dict[str, ResourceScope]] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("preset name cannot be blank")
        return value.strip()


class UserPreset(UserPresetFields):
    id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime


class UserPresetCatalog(PublicModel):
    items: list[UserPreset]
    can_manage: bool


class UserPresetCreate(PublicModel):
    operation_id: UUID
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    value: UserPresetFields


class UserPresetSave(UserPresetCreate):
    expected_revision: int = Field(ge=1)


class UserPresetRemove(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
