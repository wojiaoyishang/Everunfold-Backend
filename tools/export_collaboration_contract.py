"""Publish trusted collaboration HTTP schemas and socket frame types."""

import json
from pathlib import Path
from typing import Any

from app.modules.content.collaboration.schemas import CollaborationContract


def build_schema() -> dict[str, Any]:
    schema = CollaborationContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "2.10.0"
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
    for name, suffix, input_type, response in [
        ("listDraftVersions", "list", "version_query", "version_catalog"),
        ("readDraftVersion", "read", "version_lookup", "version_detail"),
        ("deleteDraftVersion", "delete", "version_lookup", "version_deleted"),
        ("uploadDraftBranch", "branch/upload", "branch_upload", "branch_detail"),
        ("readDraftBranch", "branch/read", "branch_lookup", "branch_detail"),
        ("deleteDraftBranch", "branch/delete", "branch_lookup", "version_deleted"),
        ("beginDraftMerge", "merge/begin", "merge_begin", "merge_detail"),
        ("readDraftMerge", "merge/read", "merge_lookup", "merge_detail"),
        ("resolveDraftMerge", "merge/resolve", "merge_resolve", "merge_detail"),
        ("renewDraftMerge", "merge/renew", "merge_lookup", "merge_detail"),
        ("cancelDraftMerge", "merge/cancel", "merge_lookup", "merge_detail"),
        ("finishDraftMerge", "merge/finish", "merge_lookup", "merge_detail"),
        ("deleteDraftMerge", "merge/delete", "merge_lookup", "merge_detail"),
    ]:
        schema["x-everunfold-operations"].append(
            {
                "name": name,
                "path": "/v1/admin/content/versions/" + suffix,
                "method": "POST",
                "input": input_type,
                "response": response,
            }
        )
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "contracts/collaboration-v1.schema.json"
    target.write_bytes((json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode())
