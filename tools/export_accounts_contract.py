"""Publish private account routes and DTOs from their authoritative backend models."""

import json
from pathlib import Path
from typing import Any

from app.modules.auth.accounts_schemas import AccountContract


def build_schema() -> dict[str, Any]:
    schema = AccountContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.0.0"
    schema["x-everunfold-csrf-header"] = "X-CSRF-Token"
    schema["x-everunfold-credentials"] = "include"
    operations: list[dict[str, Any]] = []
    for name, path, method, response, input_name in (
        ("readAccountProfile", "/v1/account/profile", "GET", "profile", None),
        ("saveAccountProfile", "/v1/account/profile", "PATCH", "profile", "profile_save"),
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
        if "{user_id}" in path:
            operation["path_params"] = ["user_id"]
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
