"""Export the first-party glossary plugin contract without a database or running API."""

import json
from pathlib import Path
from typing import Any

from app.plugins.glossary.schemas import GlossaryContract


def build_schema() -> dict[str, Any]:
    schema = GlossaryContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "3.7.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "readGlossaryIndex",
            "path": "/v1/plugins/everunfold.glossary/index",
            "method": "GET",
            "response": "public_index",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "readGlossaryDetail",
            "path": "/v1/plugins/everunfold.glossary/terms/{slug}",
            "method": "GET",
            "response": "public_detail",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "readContentGlossaryMatches",
            "path": "/v1/plugins/everunfold.glossary/for-content/{content_id}",
            "method": "GET",
            "response": "content_matches",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "queryGlossary",
            "path": "/v1/plugins/everunfold.glossary/admin/query",
            "method": "POST",
            "input": "query",
            "response": "list",
        },
        {
            "name": "previewGlossary",
            "path": "/v1/plugins/everunfold.glossary/admin/preview",
            "method": "POST",
            "input": "preview_input",
            "response": "preview_output",
        },
        {
            "name": "readGlossaryTerm",
            "path": "/v1/plugins/everunfold.glossary/admin/terms/{term_id}",
            "method": "GET",
            "response": "detail",
        },
        {
            "name": "createGlossaryTerm",
            "path": "/v1/plugins/everunfold.glossary/admin/terms",
            "method": "POST",
            "input": "create",
            "response": "detail",
        },
        {
            "name": "updateGlossaryTerm",
            "path": "/v1/plugins/everunfold.glossary/admin/terms/{term_id}",
            "method": "PATCH",
            "input": "update",
            "response": "detail",
        },
        {
            "name": "readGlossaryHooksForTarget",
            "path": "/v1/plugins/everunfold.glossary/hooks/for-target",
            "method": "GET",
            "response": "public_hooks",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "queryGlossaryHooks",
            "path": "/v1/plugins/everunfold.glossary/admin/hooks/query",
            "method": "POST",
            "input": "hook_query",
            "response": "hooks_query",
        },
        {
            "name": "readGlossaryHook",
            "path": "/v1/plugins/everunfold.glossary/admin/hooks/{hook_id}",
            "method": "GET",
            "response": "hook_detail",
        },
        {
            "name": "createGlossaryHook",
            "path": "/v1/plugins/everunfold.glossary/admin/hooks",
            "method": "POST",
            "input": "hook_create",
            "response": "hook_detail",
        },
        {
            "name": "updateGlossaryHook",
            "path": "/v1/plugins/everunfold.glossary/admin/hooks/{hook_id}",
            "method": "PATCH",
            "input": "hook_update",
            "response": "hook_detail",
        },
        {
            "name": "archiveGlossaryHook",
            "path": "/v1/plugins/everunfold.glossary/admin/hooks/{hook_id}/archive",
            "method": "POST",
            "input": "hook_archive",
            "response": "hook_detail",
        },
    ]
    return schema


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "contracts/glossary-v1.schema.json"
    target.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
