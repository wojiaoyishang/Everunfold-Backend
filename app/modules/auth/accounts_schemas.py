"""Private self-service and administrative account contracts."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.credentials_schemas import (
    ApplicationCreate,
    ApplicationList,
    ApplicationMutation,
    ApplicationRotate,
    ApplicationSave,
    ApplicationState,
    CallbackCommand,
    KeyCreate,
    KeyList,
    KeyMutation,
    KeyOptions,
    KeyQuery,
    KeyRevoke,
)
from app.modules.auth.permission_schemas import PermissionDefinition
from app.modules.auth.permissions import ResourceScope
from app.modules.auth.schemas import AuthSessionState
from app.modules.settings.languages import LanguageCatalog
from app.modules.settings.schemas import safe_url


class ProfileFields(PublicModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: str = Field(default="", max_length=254)
    avatar_url: str = Field(default="", max_length=2048)
    bio: str = Field(default="", max_length=2000)
    website: str = Field(default="", max_length=2048)
    preferred_locale: str = Field(default="zh-CN", min_length=2, max_length=32)

    @field_validator("display_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("display name must not be blank")
        return value.strip()

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("invalid email address")
        return value

    _urls = field_validator("avatar_url", "website")(safe_url)


class AccountProfile(ProfileFields):
    id: UUID
    username: str
    role: Literal["admin", "user"]
    active: bool
    revision: int
    auth_version: int
    must_change_password: bool
    email_verified: Literal[False] = False
    grants: dict[str, dict[str, ResourceScope]]
    created_at: datetime
    updated_at: datetime


class ProfileSave(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    value: ProfileFields


class UserCreate(PublicModel):
    operation_id: UUID
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    current_password: str = Field(min_length=1, max_length=1024)
    role: Literal["admin", "user"] = "user"
    grants: dict[str, dict[str, ResourceScope]] = Field(default_factory=dict)


class UserSave(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    value: ProfileFields
    role: Literal["admin", "user"]
    active: bool
    grants: dict[str, dict[str, ResourceScope]] = Field(default_factory=dict)
    current_password: str = Field(default="", max_length=1024)


class PasswordChange(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)
    revoke_api_keys: bool = True


class PasswordReset(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class SessionItem(PublicModel):
    id: str
    current: bool
    created_at: datetime
    expires_at: datetime


class SessionList(PublicModel):
    items: list[SessionItem]


class SessionRevoke(PublicModel):
    operation_id: UUID
    session_id: str = Field(pattern=r"^(?:[a-f0-9]{64}|others)$")


class AccountUserList(PublicModel):
    items: list[AccountProfile]
    total: int
    page: int
    page_size: int


class AccountUserQuery(PublicModel):
    q: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    active: bool | None = None


class PermissionCatalog(PublicModel):
    items: list[PermissionDefinition]


class AccountContract(PublicModel):
    languages: PublicResponse[LanguageCatalog]
    key_query: KeyQuery
    key_create: KeyCreate
    key_revoke: KeyRevoke
    key_list: PublicResponse[KeyList]
    key_mutation: PublicResponse[KeyMutation]
    key_options: PublicResponse[KeyOptions]
    application_create: ApplicationCreate
    application_save: ApplicationSave
    application_rotate: ApplicationRotate
    callback_command: CallbackCommand
    applications: PublicResponse[ApplicationList]
    application: PublicResponse[ApplicationState]
    application_mutation: PublicResponse[ApplicationMutation]
    user_query: AccountUserQuery
    profile_save: ProfileSave
    user_create: UserCreate
    user_save: UserSave
    password_change: PasswordChange
    password_reset: PasswordReset
    session_revoke: SessionRevoke
    profile: PublicResponse[AccountProfile]
    users: PublicResponse[AccountUserList]
    sessions: PublicResponse[SessionList]
    permissions: PublicResponse[PermissionCatalog]
    session: PublicResponse[AuthSessionState]
