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
        json={"comment_id": cid},
        headers=csrf,
    )
    assert found.status_code == 200, found.text
    item = found.json()["data"]["items"][0]
    assert item["id"] == cid and item["status"] == "pending"
    operation = {
        "operation_id": str(uuid4()),
        "items": [{"id": cid, "revision": item["revision"]}],
        "action": "approve",
        "pending_only": True,
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
            json={
                **operation,
                "operation_id": str(uuid4()),
                "items": [{"id": cid, "revision": 2}],
                "action": "reject",
            },
            headers=csrf,
        ).status_code
        == 409
    )
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
            "pending_only": False,
        },
        headers=csrf,
    )
    assert trashed.status_code == 200, trashed.text
    wait_for(lambda: not any(c["id"] == cid for c in snapshot()["comments"].get(target, [])))
    comments = client.get(f"/v1/contents/{target}/comments").json()["data"]["items"]
    assert comments[0]["id"] == reply_item["id"] and comments[0]["parent_id"] is None
    assert httpx.post(api_b + "/v1/admin/comments/query", json={}).status_code in {401, 403}
    assert client.get("/v1/admin/workspace").status_code == 200
    page_id = str(uuid4())
    page = client.post(
        "/v1/admin/pages/save",
        json={
            "id": page_id,
            "expected_revision": 0,
            "operation_id": str(uuid4()),
            "apply": True,
            "value": {
                "title": "Moderation page",
                "path": "/moderated-" + uuid4().hex,
                "enabled": True,
                "html": "<p>Page</p>",
                "captcha_comment": "off",
            },
        },
        headers=csrf,
    )
    assert page.status_code == 200, page.text
    submit = {**submit, "content_id": page_id, "operation_id": str(uuid4()), "body": "Page comment"}
    assert client.post("/v1/reader/comments", json=submit).status_code == 200
    comment_id = submit["operation_id"]
    decision = {
        "operation_id": str(uuid4()),
        "items": [{"id": comment_id, "revision": 1}],
        "action": "approve",
    }
    assert (
        client.post("/v1/admin/comments/moderate", json=decision, headers=csrf).status_code == 200
    )
    wait_for(lambda: bool(snapshot()["comments"].get(page_id)))
    assert (
        client.get(f"/v1/contents/{page_id}/comments").json()["data"]["items"][0]["id"]
        == comment_id
    )
    liked = client.put(
        f"/v1/comments/{comment_id}/like",
        json={"liked": True},
        headers={"X-Everunfold-Visitor": str(uuid4())},
    )
    assert liked.status_code == 200 and liked.json()["data"]["count"] == 1, liked.text
    edit = {
        "id": comment_id,
        "revision": 2,
        "body": "Edited page comment",
        "operation_id": str(uuid4()),
    }
    assert client.post("/v1/admin/comments/edit", json=edit, headers=csrf).status_code == 200
    for version, action in [(3, "spam"), (4, "trash"), (5, "restore")]:
        result = client.post(
            "/v1/admin/comments/moderate",
            json={
                "operation_id": str(uuid4()),
                "items": [{"id": comment_id, "revision": version}],
                "action": action,
            },
            headers=csrf,
        )
        assert result.status_code == 200, result.text
    assert result.json()["data"]["items"][0]["status"] == "spam"
    wait_for(lambda: not snapshot()["comments"].get(page_id))
