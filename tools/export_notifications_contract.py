"""Export notification administration and inbox contracts offline."""

import json
from pathlib import Path
from typing import Any

from app.modules.notifications.schemas import NotificationsContract


def build_schema() -> dict[str, Any]:
    schema = NotificationsContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.4.0"
    schema["x-everunfold-operations"] = [
        {
            "name": "saveMailBrand",
            "path": "/v1/admin/notifications/mail-brand",
            "method": "PATCH",
            "input": "brand",
            "response": "state",
        },
        {
            "name": "readNotificationState",
            "path": "/v1/admin/notifications/state",
            "method": "GET",
            "response": "state",
        },
        {
            "name": "saveNotificationRoutes",
            "path": "/v1/admin/notifications/routes",
            "method": "PATCH",
            "input": "routes",
            "response": "state",
        },
        {
            "name": "saveNotificationTemplate",
            "path": "/v1/admin/notifications/templates",
            "method": "PATCH",
            "input": "template",
            "response": "state",
        },
        {
            "name": "readNotificationInbox",
            "path": "/v1/admin/notifications/inbox",
            "method": "GET",
            "response": "inbox",
        },
        {
            "name": "markNotificationsRead",
            "path": "/v1/admin/notifications/inbox",
            "method": "PATCH",
            "input": "mark_read",
            "response": "inbox",
        },
    ]
    return schema


def main() -> None:
    schema = build_schema()
    target = Path(__file__).resolve().parents[1] / "contracts" / "notifications-v1.schema.json"
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
