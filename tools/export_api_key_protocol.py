"""The signed service protocol is independent from browser account-management DTOs."""

import json
from pathlib import Path
from typing import Any

from app.modules.auth.application_signatures import PROTOCOL, WINDOW_SECONDS
from app.modules.auth.credentials_schemas import ApiKeyProtocol


def build_schema() -> dict[str, Any]:
    schema = ApiKeyProtocol.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.0.0"
    schema["x-everunfold-signature-protocol"] = PROTOCOL
    schema["x-everunfold-signature-window-seconds"] = WINDOW_SECONDS
    schema["x-everunfold-query-path"] = "/v1/auth/api-keys/introspect"
    return schema


if __name__ == "__main__":
    Path("contracts/api-key-protocol-v1.schema.json").write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
