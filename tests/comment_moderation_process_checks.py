"""HTTP moderation, replica retries and public withdrawal in the isolated process suite."""

from typing import Any
from uuid import uuid4

import httpx2 as httpx


def check_comments(client: Any, csrf: Any, api_b: str, snapshot: Any, wait_for: Any) -> None:
    fields = {
        "title": "Moderation test",
        "body": [{"id": str(uuid4()), "type": "markdown", "content": "Public moderation target"}],
        "settings": {"path": "/moderation-" + uuid4().hex, "captcha_comment": "off"},
    }
    draft = client.post(
        "/v1/admin/content/create",
        json={"type_id": "article", "operation_id": str(uuid4()), "fields": fields},
        headers=csrf,
    ).json()["data"]
    published = client.post(
        "/v1/admin/content/publish",
        json={
            "id": draft["id"],
            "expected_revision": draft["revision"],
            "operation_id": str(uuid4()),
            "fields": fields,
        },
        headers=csrf,
    )
    assert published.status_code == 200, published.text
    target = draft["id"]
    submit = {
        "content_id": target,
        "operation_id": str(uuid4()),
        "client_id": str(uuid4()),
        "author": "Reader <script>",
        "body": "Moderation original",
    }
    response = client.post("/v1/reader/comments", json=submit)
    assert response.status_code == 200, response.text
    cid = submit["operation_id"]
    assert not any(c["id"] == cid for c in snapshot()["comments"].get(target, []))
    found = client.post(
        "/v1/admin/comments/query",
        json={"status": "pending", "search": "Moderation original"},
        headers=csrf,
    )
    assert found.status_code == 200, found.text
    item = found.json()["data"]["items"][0]
    operation = {
        "operation_id": str(uuid4()),
        "items": [{"id": cid, "revision": item["revision"]}],
        "action": "approve",
    }
    result = client.post("/v1/admin/comments/moderate", json=operation, headers=csrf)
    assert result.status_code == 200, result.text
    duplicate = httpx.post(
        api_b + "/v1/admin/comments/moderate",
        json=operation,
        headers={**csrf, "Origin": client.headers["Origin"]},
        cookies=client.cookies,
    )
    assert duplicate.status_code == 200 and duplicate.json() == result.json(), duplicate.text
    assert (
        client.post(
            "/v1/admin/comments/moderate",
            json={**operation, "operation_id": str(uuid4()), "action": "spam"},
            headers=csrf,
        ).status_code
        == 409
    )
    wait_for(lambda: any(c["id"] == cid for c in snapshot()["comments"].get(target, [])))
    reply = client.post(
        "/v1/admin/comments/reply",
        json={"operation_id": str(uuid4()), "id": cid, "revision": 2, "body": "Moderator reply"},
        headers=csrf,
    )
    assert reply.status_code == 200, reply.text
    # A mixed stale batch must not partially change its first row.
    reply_item = reply.json()["data"]["items"][0]
    stale = {
        "operation_id": str(uuid4()),
        "items": [{"id": reply_item["id"], "revision": 1}, {"id": cid, "revision": 1}],
        "action": "trash",
    }
    assert client.post("/v1/admin/comments/moderate", json=stale, headers=csrf).status_code == 409
    trashed = client.post(
        "/v1/admin/comments/moderate",
        json={
            **operation,
            "operation_id": str(uuid4()),
            "items": [{"id": cid, "revision": 2}],
            "action": "trash",
        },
        headers=csrf,
    )
    assert trashed.status_code == 200, trashed.text
    wait_for(lambda: not any(c["id"] == cid for c in snapshot()["comments"].get(target, [])))
    comments = client.get(f"/v1/contents/{target}/comments").json()["data"]["items"]
    assert comments[0]["id"] == reply_item["id"] and comments[0]["parent_id"] is None
    assert httpx.post(api_b + "/v1/admin/comments/query", json={}).status_code in {401, 403}
    assert client.get("/v1/admin/workspace").status_code == 200
