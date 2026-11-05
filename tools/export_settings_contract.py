"""Export settings types/operations without accessing live configuration or a database."""

import json
from pathlib import Path
from typing import Any

from app.modules.settings.schemas import SettingsContract
from app.modules.shortcodes.registry import builtin_catalog


def build_schema() -> dict[str, Any]:
    schema = SettingsContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.10.0"
    schema["x-everunfold-shortcodes"] = [item.model_dump() for item in builtin_catalog()]
    schema["x-everunfold-operations"] = [
        {
            "name": "readShortcodes",
            "path": "/v1/admin/shortcodes",
            "method": "GET",
            "response": "shortcode_catalog",
        },
        {
            "name": "saveShortcode",
            "path": "/v1/admin/shortcodes/save",
            "method": "POST",
            "input": "save_shortcode",
            "response": "shortcode_item",
        },
        {
            "name": "previewShortcode",
            "path": "/v1/admin/shortcodes/preview",
            "method": "POST",
            "input": "preview_shortcode",
            "response": "shortcode_preview",
        },
        {
            "name": "applyShortcode",
            "path": "/v1/admin/shortcodes/apply",
            "method": "POST",
            "input": "shortcode_action",
            "response": "shortcode_catalog",
        },
        {
            "name": "archiveShortcode",
            "path": "/v1/admin/shortcodes/archive",
            "method": "POST",
            "input": "shortcode_action",
            "response": "shortcode_catalog",
        },
        {
            "name": "restoreShortcode",
            "path": "/v1/admin/shortcodes/restore",
            "method": "POST",
            "input": "restore_shortcode",
            "response": "shortcode_item",
        },
        {
            "name": "queryFonts",
            "path": "/v1/admin/fonts/query",
            "method": "POST",
            "input": "icon_query",
            "response": "fonts",
        },
        {
            "name": "previewMediaRemoval",
            "path": "/v1/admin/media/remove-preview",
            "method": "POST",
            "input": "media_removal_intent",
            "response": "media_removal_preview",
        },
        {
            "name": "removeMedia",
            "path": "/v1/admin/media/remove",
            "method": "POST",
            "input": "media_remove",
            "response": "media_removal",
        },
        {
            "name": "restoreMedia",
            "path": "/v1/admin/media/restore",
            "method": "POST",
            "input": "media_lookup",
            "response": "media_removal",
        },
        {
            "name": "readMedia",
            "path": "/v1/admin/media/detail",
            "method": "POST",
            "input": "media_lookup",
            "response": "media_details",
        },
        {
            "name": "updateMedia",
            "path": "/v1/admin/media/update",
            "method": "POST",
            "input": "media_update",
            "response": "media_details",
        },
        {
            "name": "editMedia",
            "path": "/v1/admin/media/edit",
            "method": "POST",
            "input": "media_edit",
            "response": "media_details",
        },
        {
            "name": "queryMedia",
            "path": "/v1/admin/media/query",
            "method": "POST",
            "input": "media_query",
            "response": "media_catalog",
        },
        {
            "name": "uploadMedia",
            "path": "/v1/admin/media/upload",
            "method": "POST",
            "input": "media_upload",
            "encoding": "binary",
            "response": "media_item",
        },
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
