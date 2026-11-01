"""P0 checks against two real API processes and an isolated PostgreSQL database."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx


def check_content_drafts(
    client: Any, csrf: dict[str, str], cookies: Any, api_a: str, api_b: str, sql: Any
) -> None:
    prefix = "/v1/admin/content/"

    def post(action: str, payload: dict[str, Any]) -> Any:
        return client.post(prefix + action, json=payload, headers=csrf)

    assert httpx.get(api_a + prefix + "types").status_code == 401
    assert client.post(prefix + "query", json={"type_id": "article"}).status_code == 403
    assert post("query", {"type_id": "unknown"}).status_code == 422
    definitions = client.get(prefix + "types").json()["data"]["items"]
    assert [row["id"] for row in definitions] == ["article", "shuoshuo"]
    body = [{"id": str(uuid4()), "type": "markdown", "content": "**Initial draft**"}]
    payload: dict[str, Any] = {
        "operation_id": str(uuid4()),
        "type_id": "article",
        "fields": {"title": "Draft process proof", "excerpt": "Find this draft", "body": body},
    }

    def replicated(job: tuple[str, str, dict[str, Any]]) -> Any:
        base, action, value = job
        return httpx.post(
            base + prefix + action,
            json=value,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(replicated, [(api_a, "create", payload), (api_b, "create", payload)])
        )
    assert [result.status_code for result in results] == [200, 200], [r.text for r in results]
    first = results[0].json()["data"]
    assert results[1].json()["data"] == first
    identity = first["id"]
    assert post("create", {**payload, "type_id": "shuoshuo"}).status_code == 409
    assert (
        post(
            "create", {**payload, "operation_id": str(uuid4()), "fields": {"body": body}}
        ).status_code
        == 422
    )
    moment = post(
        "create", {"operation_id": str(uuid4()), "type_id": "shuoshuo", "fields": {"body": body}}
    )
    assert moment.status_code == 200
    assert not moment.json()["data"]["title"]
    assert post("query", {"type_id": "shuoshuo", "search": "Initial"}).json()["data"]["total"] == 1
    assert post("query", {"type_id": "article", "search": "process"}).json()["data"]["total"] == 1
    assert post("query", {"type_id": "article", "search": "%"}).json()["data"]["total"] == 0
    assert (
        post("query", {"type_id": "article", "page": 2, "page_size": 1}).json()["data"]["items"]
        == []
    )
    update: dict[str, Any] = {
        "id": identity,
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "fields": {**payload["fields"], "title": "Updated draft"},
    }
    competing = {
        **update,
        "operation_id": str(uuid4()),
        "fields": {**update["fields"], "title": "Other editor"},
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replicated, [(api_a, "save", update), (api_b, "save", competing)]))
    assert sorted(result.status_code for result in results) == [200, 409]
    current = post("detail", {"id": identity}).json()["data"]
    assert current["revision"] == 2 and current["body"][0]["id"] == body[0]["id"]
    # Audit storage failure rolls back both the row and the receipt, allowing the same retry.
    asyncio.run(
        sql(
            "ALTER TABLE content_draft_revisions ADD CONSTRAINT test_draft_audit_failure "
            "CHECK (revision < 3)"
        )
    )
    update = {**update, "operation_id": str(uuid4()), "expected_revision": 2}
    assert post("save", update).status_code == 503
    assert post("detail", {"id": identity}).json()["data"]["revision"] == 2
    asyncio.run(sql("ALTER TABLE content_draft_revisions DROP CONSTRAINT test_draft_audit_failure"))
    assert post("save", update).status_code == 200
    removal = {"id": identity, "operation_id": str(uuid4()), "expected_revision": 3}
    trashed = post("trash", removal)
    assert trashed.status_code == 200 and trashed.json()["data"]["status"] == "trash"
    assert post("trash", removal).json()["data"] == trashed.json()["data"]
    assert post("query", {"type_id": "article"}).json()["data"]["total"] == 0
    assert post("query", {"type_id": "article", "status": "trash"}).json()["data"]["total"] == 1
    assert (
        post("save", {**update, "operation_id": str(uuid4()), "expected_revision": 4}).status_code
        == 409
    )
    restored = post("restore", {**removal, "operation_id": str(uuid4()), "expected_revision": 4})
    assert restored.status_code == 200 and restored.json()["data"]["status"] == "draft"
    assert post("detail", {"id": str(uuid4())}).status_code == 404
    # Neither published contents nor public search/snapshot sources receive working drafts.
    assert (
        asyncio.run(sql("SELECT count(*) AS n FROM contents WHERE id=$1", UUID(identity)))[0]["n"]
        == 0
    )
    assert (
        asyncio.run(
            sql(
                "SELECT count(*) AS n FROM content_draft_revisions WHERE draft_id=$1",
                UUID(identity),
            )
        )[0]["n"]
        == 5
    )
    assert (
        asyncio.run(
            sql(
                "SELECT count(*) AS n FROM content_draft_receipts WHERE draft_id=$1", UUID(identity)
            )
        )[0]["n"]
        == 5
    )
    # Permission is checked on every request, including reads with an existing valid cookie.
    _check_media_history(client, csrf, post)
    asyncio.run(sql("UPDATE users SET role='user' WHERE username='admin'"))
    try:
        assert client.get(prefix + "types").status_code == 403
        assert post("detail", {"id": identity}).status_code == 403
    finally:
        asyncio.run(sql("UPDATE users SET role='admin' WHERE username='admin'"))


def _check_media_history(client: Any, csrf: dict[str, str], post: Any) -> None:
    uploaded = client.post(
        "/v1/admin/media/upload",
        params={"filename": "draft-reference.txt", "upload_id": str(uuid4())},
        content=("Draft media reference " + str(uuid4())).encode(),
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    media = uploaded.json()["data"]
    fields: dict[str, Any] = {
        "title": "Media reference proof",
        "body": [{"id": str(uuid4()), "content": f"[附件]({media['url']})"}],
    }
    created = post("create", {"operation_id": str(uuid4()), "type_id": "article", "fields": fields})
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    metadata = client.post("/v1/admin/media/detail", json={"id": media["id"]}, headers=csrf).json()[
        "data"
    ]
    intent = {"id": media["id"], "expected_revision": metadata["revision"]}
    assert (
        client.post("/v1/admin/media/remove-preview", json=intent, headers=csrf).status_code == 409
    )
    clean = post(
        "save",
        {
            "id": draft["id"],
            "operation_id": str(uuid4()),
            "expected_revision": draft["revision"],
            "fields": {
                "title": "Removed current reference",
                "body": [{"id": fields["body"][0]["id"], "content": "No current reference"}],
            },
        },
    )
    assert clean.status_code == 200
    # History still needs the original, but the current reference can be recycled.
    assert (
        client.post("/v1/admin/media/remove-preview", json=intent, headers=csrf).status_code == 200
    )
    assert (
        client.post(
            "/v1/admin/media/remove-preview", json={**intent, "delete_original": True}, headers=csrf
        ).status_code
        == 409
    )
