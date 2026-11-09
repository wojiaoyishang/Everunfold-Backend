"""Publish the page management and deployment manifest contract without runtime I/O."""

import json
from pathlib import Path
from typing import Any

from app.modules.pages.schemas import PagesContract


def build_schema() -> dict[str, Any]:
    schema = PagesContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.8.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "setPageEnabled",
            "path": "/v1/admin/pages/enabled",
            "method": "POST",
            "input": "set_enabled",
            "response": "item",
        },
        {
            "name": "readPageTranslations",
            "path": "/v1/admin/pages/translations/query",
            "method": "POST",
            "input": "translation_lookup",
            "response": "translations",
        },
        {
            "name": "createPageTranslation",
            "path": "/v1/admin/pages/translations/create",
            "method": "POST",
            "input": "create_translation",
            "response": "translations",
        },
        {
            "name": "linkPageTranslation",
            "path": "/v1/admin/pages/translations/link",
            "method": "POST",
            "input": "link_translation",
            "response": "translations",
        },
        {
            "name": "unlinkPageTranslation",
            "path": "/v1/admin/pages/translations/unlink",
            "method": "POST",
            "input": "translation_mutation",
            "response": "translations",
        },
        {
            "name": "reviewPageTranslation",
            "path": "/v1/admin/pages/translations/review",
            "method": "POST",
            "input": "translation_mutation",
            "response": "translations",
        },
        {
            "name": "readPageMetadata",
            "path": "/v1/admin/pages/{page_id}/metadata",
            "method": "GET",
            "response": "metadata",
        },
        {
            "name": "savePageMetadata",
            "path": "/v1/admin/pages/{page_id}/metadata",
            "method": "PATCH",
            "input": "save_metadata",
            "response": "metadata",
        },
        {
            "name": "readManagedPage",
            "path": "/v1/admin/pages/{page_id}",
            "method": "GET",
            "response": "item",
        },
        {
            "name": "readManagedPages",
            "path": "/v1/admin/pages",
            "method": "GET",
            "response": "catalog",
        },
        {
            "name": "saveManagedPage",
            "path": "/v1/admin/pages/save",
            "method": "POST",
            "input": "save",
            "response": "item",
        },
        {
            "name": "removeManagedPage",
            "path": "/v1/admin/pages/remove",
            "method": "POST",
            "input": "remove",
            "response": "item",
        },
    ]
    return schema


def main() -> None:
    (Path(__file__).resolve().parents[1] / "contracts/pages-v1.schema.json").write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
