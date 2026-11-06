"""Application ceilings and live owner permissions, independent of authentication transport."""

from typing import cast

from app.core.i18n import i18n
from app.modules.auth.credentials_models import ApiApplication, ApiKey
from app.modules.auth.models import User
from app.modules.auth.permission_schemas import PermissionDefinition
from app.modules.auth.permissions import (
    BUILTIN_APP,
    CAPABILITIES,
    ResourceScope,
    intersect_permissions,
    user_permissions,
)


def application_scopes(app: ApiApplication) -> list[PermissionDefinition]:
    if app.id == BUILTIN_APP:
        return [
            PermissionDefinition(
                code=code, label=i18n.message(label), api_key=key, owner_scope=owner
            )
            for code, (label, key, owner) in CAPABILITIES.items()
        ]
    scopes = [PermissionDefinition.model_validate(item) for item in app.scopes]
    if app.audience == BUILTIN_APP:
        return [
            PermissionDefinition(
                code=scope.code,
                label=i18n.message(CAPABILITIES[scope.code][0]),
                api_key=True,
                owner_scope=CAPABILITIES[scope.code][2],
            )
            for scope in scopes
            if scope.code in CAPABILITIES and CAPABILITIES[scope.code][1]
        ]
    return scopes


def owner_permissions(user: User, app: ApiApplication) -> dict[str, ResourceScope]:
    scopes = application_scopes(app)
    if user.role == "admin" and user.active and not user.must_change_password:
        available: dict[str, ResourceScope] = {scope.code: "all" for scope in scopes}
    else:
        available = user_permissions(user, app.audience)
    return {
        scope.code: available[scope.code]
        for scope in scopes
        if scope.api_key and scope.code in available
    }


def effective_permissions(user: User, app: ApiApplication, key: ApiKey) -> dict[str, ResourceScope]:
    if not app.active or key.audience != app.audience or user.key_epoch != key.user_key_epoch:
        return {}
    scopes = {scope.code: scope for scope in application_scopes(app)}
    grants = {
        name: scope
        for name, scope in key.permissions.items()
        if scope in {"own", "all"}
        and name in scopes
        and (scope != "own" or scopes[name].owner_scope)
    }
    return intersect_permissions(
        owner_permissions(user, app), cast(dict[str, ResourceScope], grants)
    )
