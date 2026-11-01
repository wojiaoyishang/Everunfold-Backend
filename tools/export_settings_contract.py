"""Export settings types/operations without accessing live configuration or a database."""

import json
from pathlib import Path
from typing import Any

from app.modules.settings.schemas import SettingsContract


def build_schema() -> dict[str, Any]:
    schema = SettingsContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.1.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "queryIcons",
            "path": "/v1/admin/icons/query",
            "method": "POST",
            "input": "icon_query",
            "response": "icons",
        },
        {
            "name": "uploadIcon",
            "path": "/v1/admin/icons/upload",
            "method": "POST",
            "input": "icon_upload",
            "response": "icon",
        },
        {
            "name": "readSettings",
            "path": "/v1/admin/settings",
            "method": "GET",
            "response": "settings",
        },
        {
            "name": "saveSettings",
            "path": "/v1/admin/settings",
            "method": "PATCH",
            "input": "save_settings",
            "response": "settings",
        },
        {
            "name": "readFrontendNodes",
            "path": "/v1/admin/frontend-nodes",
            "method": "GET",
            "response": "nodes",
        },
        {
            "name": "readCachePolicy",
            "path": "/v1/admin/frontend-nodes/{node_id}/cache-policy",
            "method": "GET",
            "response": "policy",
        },
        {
            "name": "saveCachePolicy",
            "path": "/v1/admin/frontend-nodes/{node_id}/cache-policy",
            "method": "PATCH",
            "input": "save_policy",
            "response": "policy",
        },
        {
            "name": "retrySettingsSync",
            "path": "/v1/admin/frontend-nodes/{node_id}/sync",
            "method": "POST",
            "response": "nodes",
        },
    ]
    return schema


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "contracts/settings-v1.schema.json"
    target.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
