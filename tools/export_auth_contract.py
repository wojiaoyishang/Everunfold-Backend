"""Publish authentication types and operation metadata without accessing a database."""

import json
from pathlib import Path
from typing import Any

from app.modules.auth.schemas import AuthContract


def build_schema() -> dict[str, Any]:
    schema = AuthContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "0.7.0"
    schema["x-everunfold-csrf-header"] = "X-CSRF-Token"
    schema["x-everunfold-credentials"] = "include"
    schema["x-everunfold-operations"] = [
        {
            "name": "readRecoveryOptions",
            "path": "/v1/auth/password-recovery/options",
            "method": "GET",
            "response": "recovery_options",
        },
        {
            "name": "requestPasswordRecovery",
            "path": "/v1/auth/password-recovery/request",
            "method": "POST",
            "input": "recovery_request",
            "response": "recovery_accepted",
            "csrf": True,
        },
        {
            "name": "confirmPasswordRecovery",
            "path": "/v1/auth/password-recovery/confirm",
            "method": "POST",
            "input": "recovery_confirm",
            "response": "recovery_accepted",
            "csrf": True,
        },
        {
            "name": "readRegistrationOptions",
            "path": "/v1/auth/registration/options",
            "method": "GET",
            "response": "registration_options",
        },
        {
            "name": "readRegistrationStatus",
            "path": "/v1/auth/registration/status",
            "method": "GET",
            "response": "registration_state",
        },
        {
            "name": "requestRegistration",
            "path": "/v1/auth/registration/request",
            "method": "POST",
            "input": "registration_request",
            "response": "registration_state",
            "csrf": True,
        },
        {
            "name": "confirmRegistration",
            "path": "/v1/auth/registration/confirm",
            "method": "POST",
            "input": "registration_confirm",
            "response": "session",
            "csrf": True,
        },
        {
            "name": "readAuthSession",
            "path": "/v1/auth/session",
            "method": "GET",
            "response": "session",
        },
        {
            "name": "login",
            "path": "/v1/auth/login",
            "method": "POST",
            "input": "login",
            "response": "session",
            "csrf": True,
        },
        {
            "name": "completeSecondFactor",
            "path": "/v1/auth/login/second-factor",
            "method": "POST",
            "input": "second_factor",
            "response": "session",
            "csrf": True,
        },
        {
            "name": "readTwoFactor",
            "path": "/v1/account/two-factor",
            "method": "GET",
            "response": "two_factor_state",
        },
        {
            "name": "beginTwoFactor",
            "path": "/v1/account/two-factor/setup",
            "method": "POST",
            "input": "two_factor_begin",
            "response": "two_factor_setup",
            "csrf": True,
        },
        {
            "name": "activateTwoFactor",
            "path": "/v1/account/two-factor/activate",
            "method": "POST",
            "input": "second_factor",
            "response": "two_factor_activated",
            "csrf": True,
        },
        {
            "name": "disableTwoFactor",
            "path": "/v1/account/two-factor/disable",
            "method": "POST",
            "input": "two_factor_change",
            "response": "two_factor_state",
            "csrf": True,
        },
        {
            "name": "rotateRecoveryCodes",
            "path": "/v1/account/two-factor/recovery-codes",
            "method": "POST",
            "input": "two_factor_change",
            "response": "two_factor_activated",
            "csrf": True,
        },
        {
            "name": "logout",
            "path": "/v1/auth/logout",
            "method": "POST",
            "response": "session",
            "csrf": True,
        },
        {
            "name": "readAdminSession",
            "path": "/v1/admin/session",
            "method": "GET",
            "response": "session",
        },
    ]
    schema["x-everunfold-errors"] = {
        "unauthenticated": 40101,
        "invalid_credentials": 40110,
        "admin_required": 40301,
        "csrf_rejected": 40310,
        "rate_limited": 42901,
    }
    return schema


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "contracts" / "auth-v0.1.schema.json"
    staging = target.with_suffix(target.suffix + ".tmp")
    staging.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    staging.replace(target)


if __name__ == "__main__":
    main()
