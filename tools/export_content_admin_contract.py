"""Publish draft operations and trusted type metadata from one authoritative registry."""

import json
from pathlib import Path
from typing import Any

from app.modules.content.draft_schemas import ContentAdminContract
from app.modules.content.type_registry import content_types


def build_schema() -> dict[str, Any]:
    schema = ContentAdminContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.27.0"
    schema["x-everunfold-content-types"] = [item.model_dump() for item in content_types.all()]
    schema["x-everunfold-operations"] = [
        {
            "name": "readContentLanguages",
            "path": "/v1/admin/content/languages",
            "method": "GET",
            "response": "languages",
        },
        {
            "name": "readContentActions",
            "path": "/v1/admin/content/actions",
            "method": "GET",
            "input": "lookup",
            "response": "actions",
        },
        {
            "name": "createContentCategory",
            "path": "/v1/admin/content/categories",
            "method": "POST",
            "input": "create_category",
            "response": "editor_catalog",
        },
        {
            "name": "readEditorOptions",
            "path": "/v1/admin/content/editor-options",
            "method": "GET",
            "response": "editor_catalog",
        },
        {
            "name": "readContentTypes",
            "path": "/v1/admin/content/types",
            "method": "GET",
            "response": "types",
        },
        {
            "name": "readContentSync",
            "path": "/v1/admin/content/publication",
            "method": "GET",
            "response": "sync_state",
        },
        {
            "name": "retryContentSync",
            "path": "/v1/admin/content/sync",
            "method": "POST",
            "response": "sync_state",
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
                ("mutateDraftBatch", "batch", "batch", "batch_result"),
                ("queryTaxonomies", "taxonomy/query", "taxonomy_query", "taxonomy_catalog"),
                (
                    "previewTaxonomyChange",
                    "taxonomy/preview",
                    "taxonomy_command",
                    "taxonomy_impact",
                ),
                ("saveTaxonomyChange", "taxonomy/save", "taxonomy_command", "taxonomy_result"),
                ("queryDrafts", "query", "query", "catalog"),
                ("readDraft", "detail", "lookup", "detail"),
                ("createComponentDraft", "components/create", "create", "detail"),
                ("saveComponentDraft", "components/save", "save", "detail"),
                ("publishComponentDraft", "components/publish", "save", "detail"),
                ("scheduleComponentDraft", "components/schedule", "schedule", "detail"),
                ("createDraft", "create", "create", "detail"),
                ("saveDraft", "save", "save", "detail"),
                ("publishDraft", "publish", "save", "detail"),
                ("scheduleDraft", "schedule", "schedule", "detail"),
                ("cancelDraftSchedule", "cancel-schedule", "mutation", "detail"),
                ("withdrawDraft", "withdraw", "mutation", "detail"),
                ("trashDraft", "trash", "mutation", "detail"),
                ("restoreDraft", "restore", "mutation", "detail"),
                ("readTranslations", "translations/query", "lookup", "translation_catalog"),
                (
                    "createTranslation",
                    "translations/create",
                    "translation_create",
                    "translation_catalog",
                ),
                ("linkTranslation", "translations/link", "translation_link", "translation_catalog"),
                (
                    "unlinkTranslation",
                    "translations/unlink",
                    "translation_mutation",
                    "translation_catalog",
                ),
                (
                    "reviewTranslation",
                    "translations/review",
                    "translation_mutation",
                    "translation_catalog",
                ),
                (
                    "trashTranslationGroup",
                    "translations/trash",
                    "translation_group_mutation",
                    "translation_catalog",
                ),
                (
                    "restoreTranslationGroup",
                    "translations/restore",
                    "translation_group_mutation",
                    "translation_catalog",
                ),
            ]
        ],
    ]
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "contracts/content-admin-v1.schema.json"
    target.write_bytes((json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n").encode())
