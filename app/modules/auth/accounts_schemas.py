"""Private self-service and administrative account contracts."""

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.avatar_schemas import (
    AvatarCatalog,
    AvatarItem,
    AvatarPolicySave,
    AvatarPolicyState,
    AvatarRemove,
    AvatarUpload,
)
from app.modules.auth.credentials_schemas import (
    ApplicationCreate,
    ApplicationList,
    ApplicationMutation,
    ApplicationRotate,
    ApplicationSave,
    ApplicationState,
    CallbackCommand,
    KeyApplication,
    KeyCreate,
    KeyList,
    KeyMutation,
    KeyOptions,
    KeyQuery,
    KeyRevoke,
)
from app.modules.auth.permission_schemas import PermissionDefinition
from app.modules.auth.permissions import ResourceScope
from app.modules.auth.preset_schemas import (
    UserPreset,
    UserPresetCatalog,
    UserPresetCreate,
    UserPresetRemove,
    UserPresetSave,
    UserPresetSelection,
    UserPresetStamp,
)
from app.modules.auth.registration_schemas import (
    InvitationBatchCreate,
    InvitationBatchCreated,
    InvitationCreate,
    InvitationCreated,
    InvitationList,
    InvitationRevoke,
    RegistrationMode,
)
from app.modules.auth.schemas import AuthSessionState
from app.modules.captcha.schemas import CaptchaAnswer
from app.modules.settings.languages import LanguageCatalog
from app.modules.settings.schemas import safe_url


class ProfileFields(PublicModel):
    display_name: str = Field(min_length=1, max_length=100)
    email: str = Field(default="", max_length=254)
    avatar_url: str = Field(default="", max_length=2048)
    avatar_id: UUID | None = None
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
    email_verified: bool = False
    email_verification_source: Literal["mailbox", "admin"] | None = None
    email_verification_at: datetime | None = None
    grants: dict[str, dict[str, ResourceScope]]
    permission_preset: UserPresetStamp | None = None
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
    password: str = Field(min_length=12, max_length=1024, repr=False)
    role: Literal["admin", "user"] = "user"
    grants: dict[str, dict[str, ResourceScope]] = Field(default_factory=dict)
    preset: UserPresetSelection | None = None


class UserSave(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    value: ProfileFields
    role: Literal["admin", "user"]
    active: bool
    grants: dict[str, dict[str, ResourceScope]] = Field(default_factory=dict)
    preset: UserPresetSelection | None = None


class PasswordChange(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    new_password: str = Field(min_length=12, max_length=1024, repr=False)
    revoke_api_keys: bool = True


class PasswordReset(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    current_password: str = Field(min_length=1, max_length=1024, repr=False)
    new_password: str = Field(min_length=12, max_length=1024, repr=False)


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


class EmailVerificationRequest(PublicModel):
    operation_id: UUID
    captcha: CaptchaAnswer | None = None
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("invalid email address")
        return value


class EmailVerificationConfirm(PublicModel):
    request_id: UUID
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(pattern=r"^[0-9]{6}$", repr=False)

    @field_validator("email")
    @classmethod
    def email_address(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("invalid email address")
        return value


EmailVerificationStatus = Literal[
    "queued", "sending", "sent", "failed", "unknown", "expired", "verified"
]


class EmailVerificationState(PublicModel):
    request_id: UUID
    email: str
    status: EmailVerificationStatus
    expires_at: datetime
    retry_after_seconds: int = Field(ge=0)


class AccessSettingsInput(PublicModel):
    expected_revision: int = Field(ge=1)
    login_captcha: bool | None = None
    registration_mode: RegistrationMode | None = None
    registration_captcha: bool | None = None
    email_verification_captcha: bool | None = None
    mail_profile_id: UUID | None = None


class AccessSenderOption(PublicModel):
    id: UUID
    name: str


class AccessSettingsState(PublicModel):
    revision: int
    login_captcha: bool
    registration_mode: RegistrationMode
    registration_captcha: bool
    email_verification_captcha: bool
    mail_profile_id: UUID | None
    mail_ready: bool
    mail_profiles: list[AccessSenderOption]


class ReviewAccountEmail(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    action: Literal["approve", "revoke"]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value.strip()


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
    applications: list[KeyApplication] = Field(default_factory=list)


class AccountContract(PublicModel):
    user_preset: PublicResponse[UserPreset]
    user_presets: PublicResponse[UserPresetCatalog]
    user_preset_create: UserPresetCreate
    user_preset_save: UserPresetSave
    user_preset_remove: UserPresetRemove
    avatar_catalog: PublicResponse[AvatarCatalog]
    avatar_item: PublicResponse[AvatarItem]
    avatar_policy: PublicResponse[AvatarPolicyState]
    avatar_policy_save: AvatarPolicySave
    avatar_remove: AvatarRemove
    avatar_upload: AvatarUpload
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
    access_settings: PublicResponse[AccessSettingsState]
    save_access_settings: AccessSettingsInput
    invitation_create: InvitationCreate
    invitation_created: PublicResponse[InvitationCreated]
    invitation_batch_create: InvitationBatchCreate
    invitation_batch_created: PublicResponse[InvitationBatchCreated]
    invitation_list: PublicResponse[InvitationList]
    invitation_revoke: InvitationRevoke
    email_review: ReviewAccountEmail
    email_verification_request: EmailVerificationRequest
    email_verification_confirm: EmailVerificationConfirm
    profile: PublicResponse[AccountProfile]
    users: PublicResponse[AccountUserList]
    sessions: PublicResponse[SessionList]
    permissions: PublicResponse[PermissionCatalog]
    session: PublicResponse[AuthSessionState]
    email_verification: PublicResponse[EmailVerificationState]
