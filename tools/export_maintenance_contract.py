"""Generate the maintenance contract without deployment configuration or database access."""

import json
from pathlib import Path
from typing import Any

from app.modules.maintenance.schemas import MaintenanceContract


def build_schema() -> dict[str, Any]:
    schema = MaintenanceContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.0.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "readMaintenanceHealth",
            "path": "/v1/admin/maintenance/health",
            "method": "GET",
            "response": "health",
        },
        {
            "name": "readMaintenanceHistory",
            "path": "/v1/admin/maintenance/history",
            "method": "GET",
            "response": "history",
        },
        {
            "name": "exportMaintenanceBundle",
            "path": "/v1/admin/maintenance/export",
            "method": "POST",
            "input": "export_request",
            "response": "bundle",
        },
        {
            "name": "previewMaintenanceImport",
            "path": "/v1/admin/maintenance/import/preview",
            "method": "POST",
            "input": "import_request",
            "response": "preview",
        },
        {
            "name": "applyMaintenanceImport",
            "path": "/v1/admin/maintenance/import/apply",
            "method": "POST",
            "input": "apply_request",
            "response": "receipt",
        },
    ]
    return schema


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "contracts/maintenance-v1.schema.json"
    target.write_text(
        json.dumps(build_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
