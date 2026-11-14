"""Export the private moderation and workspace contract without touching a database."""

import json
from pathlib import Path
from typing import Any

from app.modules.comments.schemas import OperationsContract


def build_schema() -> dict[str, Any]:
    schema = OperationsContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.2.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "readWorkspace",
            "path": "/v1/admin/workspace",
            "method": "GET",
            "response": "workspace",
        },
        *[
            {
                "name": name,
                "path": "/v1/admin/comments/" + path,
                "method": "POST",
                "input": key,
                "response": response,
            }
            for name, path, key, response in [
                ("queryComments", "query", "query", "catalog"),
                ("moderateComments", "moderate", "moderate", "result"),
                ("editComment", "edit", "edit", "result"),
                ("replyToComment", "reply", "edit", "result"),
            ]
        ],
    ]
    return schema


if __name__ == "__main__":
    (Path(__file__).resolve().parents[1] / "contracts/operations-v1.schema.json").write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"
    )
