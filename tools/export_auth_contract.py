"""Publish authentication types and operation metadata without accessing a database."""

import json
from pathlib import Path

from app.modules.auth.schemas import AuthContract


def main() -> None:
    schema = AuthContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "0.1.0"
    schema["x-everunfold-csrf-header"] = "X-CSRF-Token"
    schema["x-everunfold-credentials"] = "include"
    schema["x-everunfold-operations"] = [
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
    target = Path(__file__).resolve().parents[1] / "contracts" / "auth-v0.1.schema.json"
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
