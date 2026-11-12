"""Publish private account routes and DTOs from their authoritative backend models."""

import json
import re
from pathlib import Path
from typing import Any

from app.modules.auth.accounts_schemas import AccountContract


def build_schema() -> dict[str, Any]:
    schema = AccountContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.11.0"
    schema["x-everunfold-csrf-header"] = "X-CSRF-Token"
    schema["x-everunfold-credentials"] = "include"
    operations: list[dict[str, Any]] = []
    for name, path, method, response, input_name in (
        ("readUserPresets", "/v1/admin/user-presets", "GET", "user_presets", None),
        ("createUserPreset", "/v1/admin/user-presets", "POST", "user_preset", "user_preset_create"),
        (
            "saveUserPreset",
            "/v1/admin/user-presets/{preset_id}",
            "PATCH",
            "user_preset",
            "user_preset_save",
        ),
        (
            "removeUserPreset",
            "/v1/admin/user-presets/{preset_id}/remove",
            "POST",
            "user_presets",
            "user_preset_remove",
        ),
        ("readAccountAvatars", "/v1/account/avatars", "GET", "avatar_catalog", None),
        (
            "uploadAccountAvatar",
            "/v1/account/avatars/upload",
            "POST",
            "avatar_item",
            "avatar_upload",
        ),
        (
            "removeAccountAvatar",
            "/v1/account/avatars/remove",
            "POST",
            "avatar_catalog",
            "avatar_remove",
        ),
        ("readAvatarPolicy", "/v1/admin/users/avatar-policy", "GET", "avatar_policy", None),
        (
            "saveAvatarPolicy",
            "/v1/admin/users/avatar-policy",
            "PATCH",
            "avatar_policy",
            "avatar_policy_save",
        ),
        ("readUserAvatars", "/v1/admin/users/{user_id}/avatars", "GET", "avatar_catalog", None),
        (
            "removeUserAvatar",
            "/v1/admin/users/{user_id}/avatars/remove",
            "POST",
            "avatar_catalog",
            "avatar_remove",
        ),
        ("readAccountLanguages", "/v1/account/languages", "GET", "languages", None),
        ("readApiKeyOptions", "/v1/account/api-keys/options", "GET", "key_options", None),
        ("readApiKeys", "/v1/account/api-keys", "GET", "key_list", "key_query"),
        ("createApiKey", "/v1/account/api-keys", "POST", "key_mutation", "key_create"),
        ("revokeApiKey", "/v1/account/api-keys/revoke", "POST", "key_list", "key_revoke"),
        ("readUserApiKeys", "/v1/admin/users/{user_id}/api-keys", "GET", "key_list", "key_query"),
        (
            "revokeUserApiKey",
            "/v1/admin/users/{user_id}/api-keys/revoke",
            "POST",
            "key_list",
            "key_revoke",
        ),
        ("readApiApplications", "/v1/admin/api-key-applications", "GET", "applications", None),
        (
            "createApiApplication",
            "/v1/admin/api-key-applications",
            "POST",
            "application_mutation",
            "application_create",
        ),
        (
            "saveApiApplication",
            "/v1/admin/api-key-applications/{app_id}",
            "PATCH",
            "application_mutation",
            "application_save",
        ),
        (
            "rotateApiApplication",
            "/v1/admin/api-key-applications/{app_id}/credentials",
            "POST",
            "application_mutation",
            "application_rotate",
        ),
        (
            "commandApiCallback",
            "/v1/admin/api-key-applications/{app_id}/callback",
            "POST",
            "application",
            "callback_command",
        ),
        ("readAccountProfile", "/v1/account/profile", "GET", "profile", None),
        ("saveAccountProfile", "/v1/account/profile", "PATCH", "profile", "profile_save"),
        ("readAccessSettings", "/v1/admin/access/settings", "GET", "access_settings", None),
        ("listInvitations", "/v1/admin/access/invitations", "GET", "invitation_list", None),
        (
            "createInvitation",
            "/v1/admin/access/invitations",
            "POST",
            "invitation_created",
            "invitation_create",
        ),
        (
            "createInvitationBatch",
            "/v1/admin/access/invitations/batch",
            "POST",
            "invitation_batch_created",
            "invitation_batch_create",
        ),
        (
            "revokeInvitation",
            "/v1/admin/access/invitations/revoke",
            "POST",
            "invitation_list",
            "invitation_revoke",
        ),
        (
            "saveAccessSettings",
            "/v1/admin/access/settings",
            "PATCH",
            "access_settings",
            "save_access_settings",
        ),
        (
            "readAccountEmailVerification",
            "/v1/account/email-verification/{request_id}",
            "GET",
            "email_verification",
            None,
        ),
        (
            "reviewAccountEmail",
            "/v1/admin/users/{user_id}/email-verification",
            "POST",
            "profile",
            "email_review",
        ),
        (
            "requestAccountEmailVerification",
            "/v1/account/email-verification/request",
            "POST",
            "email_verification",
            "email_verification_request",
        ),
        (
            "confirmAccountEmailVerification",
            "/v1/account/email-verification/confirm",
            "POST",
            "profile",
            "email_verification_confirm",
        ),
        ("changeAccountPassword", "/v1/account/password", "POST", "session", "password_change"),
        ("readAccountSessions", "/v1/account/sessions", "GET", "sessions", None),
        (
            "revokeAccountSession",
            "/v1/account/sessions/revoke",
            "POST",
            "sessions",
            "session_revoke",
        ),
        ("readAccountPermissions", "/v1/account/permissions", "GET", "permissions", None),
        ("readAccountUsers", "/v1/admin/users", "GET", "users", "user_query"),
        ("createAccountUser", "/v1/admin/users", "POST", "profile", "user_create"),
        ("readAccountUser", "/v1/admin/users/{user_id}", "GET", "profile", None),
        ("saveAccountUser", "/v1/admin/users/{user_id}", "PATCH", "profile", "user_save"),
        (
            "resetAccountUserPassword",
            "/v1/admin/users/{user_id}/password",
            "POST",
            "profile",
            "password_reset",
        ),
    ):
        operation: dict[str, Any] = {
            "name": name,
            "path": path,
            "method": method,
            "response": response,
        }
        if input_name:
            operation["input"] = input_name
        if name == "uploadAccountAvatar":
            operation["encoding"] = "binary"
        if path_params := re.findall(r"\{([a-z_]+)\}", path):
            operation["path_params"] = path_params
        operations.append(operation)
    schema["x-everunfold-operations"] = operations
    return schema


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "contracts/accounts-v1.schema.json"
    target.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
