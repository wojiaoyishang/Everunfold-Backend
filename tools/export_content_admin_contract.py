"""Publish draft operations and trusted type metadata from one authoritative registry."""

import json
from pathlib import Path
from typing import Any

from app.modules.content.draft_schemas import ContentAdminContract
from app.modules.content.type_registry import content_types


def build_schema() -> dict[str, Any]:
    schema = ContentAdminContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.0.0"
    schema["x-everunfold-content-types"] = [item.model_dump() for item in content_types.all()]
    schema["x-everunfold-operations"] = [
        {
            "name": "readContentTypes",
            "path": "/v1/admin/content/types",
            "method": "GET",
            "response": "types",
        },
        *[
            {
                "name": name,
                "path": f"/v1/admin/content/{path}",
                "method": "POST",
                "input": input_key,
                "response": response,
            }
            for name, path, input_key, response in [
                ("queryDrafts", "query", "query", "catalog"),
                ("readDraft", "detail", "lookup", "detail"),
                ("createDraft", "create", "create", "detail"),
                ("saveDraft", "save", "save", "detail"),
                ("trashDraft", "trash", "mutation", "detail"),
                ("restoreDraft", "restore", "mutation", "detail"),
            ]
        ],
    ]
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "contracts/content-admin-v1.schema.json"
    target.write_bytes((json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode())
