"""Real password boundary, time opening, title paths and native taxonomy counts."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx


def check_content_access(
    client: Any, csrf: Any, state: Any, snapshot: Any, wait_for: Any, sql: Any, api_b: str
) -> None:
    def post(action: str, value: Any) -> Any:
        response = client.post("/v1/admin/content/" + action, json=value, headers=csrf)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    root = post("categories", {"name": "联调分类"})
    parent = next(term for term in root["terms"] if term["name"] == "联调分类")
    child = post("categories", {"name": "联调子分类", "parent_id": parent["id"]})
    assert (
        next(term for term in child["terms"] if term["name"] == "联调子分类")["parent_id"]
        == parent["id"]
    )
    assert post("categories", {"name": "联调子分类", "parent_id": parent["id"]}) == child
    secret = "development-only-article-password"
    body_secret = "restricted正文不得出现在shared-cache"
    fields: dict[str, Any] = {
        "title": "新标题测试",
        "body": [{"id": str(uuid4()), "type": "markdown", "content": body_secret}],
        "settings": {
            "visibility": "password",
            "categories": ["联调子分类"],
            "tags": ["常用标签"],
            "seo_description": "private seo",
        },
        "access_password": secret,
    }
    create = {"type_id": "article", "operation_id": str(uuid4()), "fields": fields}
    draft = post("create", create)
    assert draft["has_password"] and secret not in json.dumps(draft)
    assert draft["settings"]["path"] == "/新标题测试.html"
    assert post("create", create) == draft
    second = post("create", {**create, "operation_id": str(uuid4())})
    assert second["settings"]["path"] == "/新标题测试-2.html"

    def write(current: Any, value: Any) -> Any:
        return post(
            "publish",
            {
                "id": current["id"],
                "expected_revision": current["revision"],
                "operation_id": str(uuid4()),
                "fields": value,
            },
        )

    # Omitted settings/password preserves saved policy and address.
    published = write(draft, {"title": "改标题仍保留地址", "body": fields["body"]})
    assert published["public_path"] == "/新标题测试.html"
    actions = client.get("/v1/admin/content/actions", params={"id": draft["id"]})
    assert actions.status_code == 200 and "no-store" in actions.headers["cache-control"]
    assert actions.json()["data"] == {"edit": {"id": draft["id"], "type_id": "article"}}
    assert client.get("/v1/admin/content/actions", params={"id": second["id"]}).json()["data"] == {
        "edit": None
    }
    with httpx.Client(base_url=api_b, timeout=8) as stranger:
        assert (
            stranger.get("/v1/admin/content/actions", params={"id": draft["id"]}).status_code == 401
        )
    actor_id = UUID(client.get("/v1/admin/session").json()["data"]["user"]["id"])
    asyncio.run(sql("UPDATE users SET role='user' WHERE id=$1", actor_id))
    try:
        assert (
            client.get("/v1/admin/content/actions", params={"id": draft["id"]}).status_code == 403
        )
    finally:
        asyncio.run(sql("UPDATE users SET role='admin' WHERE id=$1", actor_id))
    assert client.get("/v1/admin/content/actions", params={"id": uuid4()}).json()["data"] == {
        "edit": None
    }
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    cached = next(item for item in snapshot()["contents"] if item["id"] == draft["id"])
    assert cached["access"] == "password" and not cached["content"] and not cached["media_urls"]
    assert not cached["excerpt"] and not cached["seo_description"]
    public = client.get("/v1/contents/resolve", params={"path": published["public_path"]})
    assert body_secret not in public.text and body_secret not in json.dumps(snapshot())
    assert client.get("/v1/contents", params={"q": body_secret}).json()["data"]["total"] == 0
    origin = client.headers["Origin"]
    with httpx.Client(base_url=api_b, headers={"Origin": origin}, timeout=8) as reader:
        denied = reader.post("/v1/reader/access", json={"id": draft["id"], "password": "wrong"})
        assert denied.status_code == 403 and "no-store" in denied.headers["cache-control"]
        assert (
            reader.post(
                "/v1/reader/access",
                json={"id": draft["id"], "password": secret},
                headers={"Origin": "https://untrusted.example"},
            ).status_code
            == 403
        )
        result = reader.post("/v1/reader/access", json={"id": draft["id"], "password": secret})
        assert result.status_code == 200 and result.json()["data"]["content"] == fields["body"]
        assert "no-store" in result.headers["cache-control"] and "set-cookie" not in result.headers
        for _ in range(8):
            reader.post("/v1/reader/access", json={"id": draft["id"], "password": "wrong"})
        assert (
            reader.post(
                "/v1/reader/access", json={"id": draft["id"], "password": secret}
            ).status_code
            == 429
        )
    rows = asyncio.run(
        sql(
            "SELECT snapshot::text AS value FROM content_draft_revisions WHERE draft_id=$1 "
            "UNION ALL SELECT response::text FROM content_draft_receipts WHERE draft_id=$1",
            UUID(draft["id"]),
        )
    )
    assert secret not in json.dumps([dict(row) for row in rows])
    # Independent Worker projection refresh may fail; request-time policy must still open on time.
    due = datetime.now(UTC) + timedelta(seconds=5)
    timed = write(
        second,
        {
            "title": second["title"],
            "body": fields["body"],
            "settings": {**second["settings"], "opens_at": due.isoformat()},
        },
    )
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    assert (
        next(item for item in snapshot()["contents"] if item["id"] == timed["id"])["access"]
        == "scheduled"
    )
    asyncio.run(
        sql(
            "ALTER TABLE content_releases ADD CONSTRAINT timed_refresh_failure "
            "CHECK (revision < 0) NOT VALID"
        )
    )
    try:
        wait_for(lambda: datetime.now(UTC) >= due)
        live = client.post(
            "/v1/reader/access", json={"id": timed["id"]}, headers={"Origin": origin}
        )
        assert live.status_code == 200 and live.json()["data"]["content"] == fields["body"]
        assert (
            asyncio.run(sql("SELECT open_synced_at FROM contents WHERE id=$1", UUID(timed["id"])))[
                0
            ]["open_synced_at"]
            is None
        )
    finally:
        asyncio.run(sql("ALTER TABLE content_releases DROP CONSTRAINT timed_refresh_failure"))
    wait_for(
        lambda: (
            asyncio.run(sql("SELECT open_synced_at FROM contents WHERE id=$1", UUID(timed["id"])))[
                0
            ]["open_synced_at"]
            is not None
        )
    )
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    generation = state()["revision"]
    opened = next(item for item in snapshot()["contents"] if item["id"] == timed["id"])
    assert opened["access"] == "public" and opened["content"] == fields["body"]
    # A later update re-seals it and must replace the previously public cached body.
    sealed = write(
        timed,
        {
            "title": timed["title"],
            "body": fields["body"],
            "settings": {**timed["settings"], "opens_at": None},
        },
    )
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    assert state()["revision"] == generation + 1
    assert not next(item for item in snapshot()["contents"] if item["id"] == sealed["id"])[
        "content"
    ]
    options = client.get("/v1/admin/content/editor-options").json()["data"]["terms"]
    assert next(term for term in options if term["name"] == "常用标签")["count"] == 2
