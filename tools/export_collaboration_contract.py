"""Publish trusted collaboration HTTP schemas and socket frame types."""

import json
from pathlib import Path
from typing import Any

from app.modules.content.collaboration.schemas import CollaborationContract


def build_schema() -> dict[str, Any]:
    schema = CollaborationContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.1.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "acquireHtmlSource",
            "path": "/v1/admin/content/collaboration/source/acquire",
            "method": "POST",
            "input": "source_acquire",
            "response": "source_acquired",
        },
        {
            "name": "renewHtmlSource",
            "path": "/v1/admin/content/collaboration/source/renew",
            "method": "POST",
            "input": "source_command",
            "response": "source_acquired",
        },
        {
            "name": "releaseHtmlSource",
            "path": "/v1/admin/content/collaboration/source/release",
            "method": "POST",
            "input": "source_command",
            "response": "source_released",
        },
        {
            "name": "applyHtmlSource",
            "path": "/v1/admin/content/collaboration/source/apply",
            "method": "POST",
            "input": "source_apply",
            "response": "source_applied",
        },
        {
            "name": "createCollaborationTicket",
            "path": "/v1/admin/content/collaboration/ticket",
            "method": "POST",
            "input": "lookup",
            "response": "ticket",
        },
        {
            "name": "commitCollaborationDraft",
            "path": "/v1/admin/content/collaboration/commit",
            "method": "POST",
            "input": "commit",
            "response": "committed",
        },
    ]
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "contracts/collaboration-v1.schema.json"
    target.write_bytes((json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode())
