"""Private key/application DTOs and a separate machine-to-machine query protocol."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.core.schemas import PublicModel, PublicResponse
from app.modules.auth.permission_schemas import PermissionDefinition
from app.modules.auth.permissions import ResourceScope


class ApplicationScope(PublicModel):
    code: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.:-]*$"
    )
    label: str = Field(min_length=1, max_length=100)
    api_key: Literal[True] = True
    owner_scope: bool = False


class ApplicationFields(PublicModel):
    name: str = Field(min_length=1, max_length=100)
    active: bool = True
    callback_url: str = Field(default="", max_length=2048)
    scopes: list[ApplicationScope] = Field(default_factory=list, max_length=64)


class ApplicationCreate(PublicModel):
    operation_id: UUID
    app_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    audience: Literal["everunfold", "self"] = "everunfold"
    value: ApplicationFields
    current_password: str = Field(min_length=1, max_length=1024, repr=False)


class ApplicationSave(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    value: ApplicationFields
    current_password: str = Field(min_length=1, max_length=1024, repr=False)


class ApplicationRotate(PublicModel):
    operation_id: UUID
    expected_revision: int = Field(ge=1)
    direction: Literal["query", "callback", "both"]
    current_password: str = Field(min_length=1, max_length=1024, repr=False)


class CallbackCommand(PublicModel):
    operation_id: UUID
    action: Literal["test", "resync", "retry"]


class CallbackState(PublicModel):
    id: UUID
    event_type: str
    status: Literal["pending", "delivered", "failed", "skipped"]
    attempts: int
    created_at: datetime
    received_at: datetime | None
    applied_at: datetime | None
    last_error: str


class ApplicationState(PublicModel):
    id: str
    name: str
    audience: str
    active: bool
    callback_url: str
    scopes: list[PermissionDefinition]
    revision: int
    auth_version: int
    query_key_id: str | None
    callback_key_id: str | None
    query_url: str
    signing_available: bool
    builtin: bool
    callbacks: list[CallbackState]


class ApplicationList(PublicModel):
    items: list[ApplicationState]


class ApplicationMutation(PublicModel):
    application: ApplicationState
    query_secret: str | None = Field(default=None, repr=False)
    callback_secret: str | None = Field(default=None, repr=False)


class KeyApplication(PublicModel):
    id: str
    name: str
    audience: str
    permissions: dict[str, ResourceScope]
    scopes: list[PermissionDefinition]


class KeyOptions(PublicModel):
    applications: list[KeyApplication]


class KeyCreate(PublicModel):
    operation_id: UUID
    app_id: str = Field(default="everunfold", min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    permissions: dict[str, ResourceScope]
    expires_at: AwareDatetime
    current_password: str = Field(min_length=1, max_length=1024, repr=False)


class KeyRevoke(PublicModel):
    operation_id: UUID
    key_id: UUID


class KeyState(PublicModel):
    id: UUID
    app_id: str
    application_name: str
    audience: str
    name: str
    masked: str
    permissions: dict[str, ResourceScope]
    effective_permissions: dict[str, ResourceScope]
    status: Literal["active", "revoked", "expired", "unavailable"]
    revision: int
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyList(PublicModel):
    items: list[KeyState]
    total: int
    page: int = 1
    page_size: int = 20


class KeyQuery(PublicModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class KeyMutation(PublicModel):
    key: KeyState
    secret: str | None = Field(default=None, repr=False)


class IntrospectionInput(PublicModel):
    token: str = Field(min_length=1, max_length=160, repr=False)
    mode: Literal["read", "write"] = "read"


class KeySubject(PublicModel):
    id: UUID
    display_name: str


class IntrospectionState(PublicModel):
    active: bool
    app_id: str | None = None
    audience: str | None = None
    key_id: UUID | None = None
    subject: KeySubject | None = None
    permissions: dict[str, ResourceScope] | None = None
    app_version: int | None = None
    user_version: int | None = None
    key_version: int | None = None
    checked_at: datetime | None = None
    expires_at: datetime | None = None
    cache_until: datetime | None = None


class CallbackReceipt(PublicModel):
    event_id: UUID
    app_id: str
    app_version: int = Field(ge=1)
    received: Literal[True]
    applied: bool


class CallbackEnvelope(PublicModel):
    protocol: Literal["everunfold.api-key.v1"]
    event_id: UUID
    app_id: str
    audience: str
    type: Literal[
        "key.revoked",
        "user.authorization_changed",
        "application.authorization_changed",
        "application.credentials_rotated",
        "application.test",
        "application.resync",
    ]
    app_version: int = Field(ge=1)
    occurred_at: AwareDatetime
    user_id: UUID | None = None
    user_version: int | None = Field(default=None, ge=1)
    key_id: UUID | None = None
    key_version: int | None = Field(default=None, ge=1)


class ApiKeyProtocol(PublicModel):
    query: IntrospectionInput
    result: PublicResponse[IntrospectionState]
    callback: CallbackEnvelope
    receipt: CallbackReceipt
