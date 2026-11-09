"""Metadata transactions across two real API processes, including replay and rollback."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx2 as httpx


def check_content_management(client: Any, csrf: Any, cookies: Any, api_a: str, api_b: str) -> None:
    prefix = "/v1/admin/content/"

    def post(action: str, value: Any) -> Any:
        return client.post(prefix + action, json=value, headers=csrf)

    drafts = []
    for index in range(2):
        result = post(
            "create",
            {
                "operation_id": str(uuid4()),
                "type_id": "article",
                "fields": {
                    "title": f"Atomic metadata {index}",
                    "body": [
                        {"id": str(uuid4()), "type": "markdown", "content": "Body must remain"}
                    ],
                    "settings": {"tags": ["Atomic tag"]},
                },
            },
        )
        assert result.status_code == 200, result.text
        drafts.append(result.json()["data"])
    payload = {
        "operation_id": str(uuid4()),
        "action": "edit",
        "items": [{"id": d["id"], "expected_revision": d["revision"]} for d in drafts],
        "patch": {"pinned": True},
    }

    def invoke(base: str) -> Any:
        return httpx.post(
            base + prefix + "batch",
            json=payload,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=15,
        )

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(invoke, [api_a, api_b]))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert results[0].json()["data"] == results[1].json()["data"]
    updated = results[0].json()["data"]["items"]
    assert all(row["revision"] == 2 and row["settings"]["pinned"] for row in updated)
    bodies = {row["id"]: row["body"] for row in drafts}
    assert all(row["body"] == bodies[row["id"]] for row in updated)
    conflict = {
        **payload,
        "operation_id": str(uuid4()),
        "items": [
            {"id": updated[0]["id"], "expected_revision": 2},
            {"id": updated[1]["id"], "expected_revision": 1},
        ],
        "patch": {"comments_open": False},
    }
    assert post("batch", conflict).status_code == 409
    for row in updated:
        current = post("detail", {"id": row["id"]}).json()["data"]
        assert current["revision"] == 2 and current["settings"]["comments_open"]
    terms = post("taxonomy/query", {"kind": "tag", "search": "Atomic tag"}).json()["data"]
    term = next(t for t in terms["items"] if t["name"] == "Atomic tag")
    rename = {
        "operation_id": str(uuid4()),
        "action": "update",
        "kind": "tag",
        "id": term["id"],
        "expected_revision": term["revision"],
        "name": "Atomic renamed",
    }
    preview = post("taxonomy/preview", rename).json()["data"]
    assert preview["drafts"] == 2
    mutation = post(
        "batch",
        {
            **conflict,
            "operation_id": str(uuid4()),
            "items": [{"id": updated[0]["id"], "expected_revision": 2}],
            "patch": {"pinned": False},
        },
    )
    assert mutation.status_code == 200, mutation.text
    stale = post("taxonomy/save", {**rename, "impact_token": preview["token"]})
    assert stale.status_code == 409 and stale.json()["code"] == 40990
    preview = post("taxonomy/preview", rename).json()["data"]
    rename["impact_token"] = preview["token"]
    assert post("taxonomy/save", rename).status_code == 200
    for row in updated:
        assert post("detail", {"id": row["id"]}).json()["data"]["settings"]["tags"] == [
            "Atomic renamed"
        ]
    assert post("taxonomy/save", rename).status_code == 200
