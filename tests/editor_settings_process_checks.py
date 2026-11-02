"""Editor metadata must reach the real Node snapshot without leaking private drafts."""

from typing import Any
from uuid import uuid4


def check_editor_settings(client: Any, csrf: Any, state: Any, snapshot: Any, wait_for: Any) -> None:
    def post(action: str, value: Any) -> Any:
        return client.post("/v1/admin/content/" + action, json=value, headers=csrf)

    uploaded = client.post(
        "/v1/admin/media/upload",
        params={"filename": "editor-cover.svg", "upload_id": str(uuid4())},
        content=(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
            b'<rect width="20" height="20" fill="red"/></svg>'
        ),
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    media = uploaded.json()["data"]
    media_id = media["id"].removeprefix("media:")
    fields: dict[str, Any] = {
        "title": "Editor properties proof",
        "body": [
            {
                "id": str(uuid4()),
                "type": "markdown",
                "content": f"![示例](site://media/{media_id})",
            },
            {"id": str(uuid4()), "type": "html", "content": "<p>Second stable block</p>"},
        ],
        "settings": {
            "path": "/editor-proof.html",
            "cover_media_id": media_id,
            "display_published_at": "2026-01-02T03:04:00Z",
            "categories": ["测试分类"],
            "tags": ["测试标签"],
            "pinned": True,
            "comments_open": False,
            "seo_title": "SEO proof",
            "seo_description": "SEO description",
        },
    }
    created = post("create", {"type_id": "article", "operation_id": str(uuid4()), "fields": fields})
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    assert draft["media_urls"][media_id]["original"] == media["url"]

    def write(action: str, value: Any, current: Any) -> Any:
        return post(
            action,
            {
                "id": current["id"],
                "expected_revision": current["revision"],
                "operation_id": str(uuid4()),
                "fields": value,
            },
        )

    result = write("publish", fields, draft)
    assert result.status_code == 200, result.text
    draft = result.json()["data"]
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    visible = next(item for item in snapshot()["contents"] if item["id"] == draft["id"])
    assert visible["cover_url"] == media["url"] and not visible["comments_open"]
    assert visible["seo_title"] == "SEO proof" and visible["pinned"]
    assert visible["categories"][0]["name"] == "测试分类"
    assert visible["tags"][0]["name"] == "测试标签"
    assert visible["content"] == fields["body"] and visible["media_urls"] == draft["media_urls"]
    assert visible["published_at"].startswith("2026-01-02T03:04:00")
    detail = client.post("/v1/admin/media/detail", json={"id": media["id"]}, headers=csrf).json()[
        "data"
    ]
    assert (
        client.post(
            "/v1/admin/media/remove-preview",
            json={"id": media["id"], "expected_revision": detail["revision"]},
            headers=csrf,
        ).status_code
        == 409
    )
    changed = {
        **fields,
        "settings": {**fields["settings"], "path": "/editor-new.html"},
        "body": list(reversed(fields["body"])),
    }
    draft = write("save", changed, draft).json()["data"]
    assert (
        client.get("/v1/contents/resolve", params={"path": "/editor-proof.html"}).status_code == 200
    )
    assert (
        client.get("/v1/contents/resolve", params={"path": "/editor-new.html"}).status_code == 404
    )
    draft = write("publish", changed, draft).json()["data"]
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    assert (
        client.get("/v1/contents/resolve", params={"path": "/editor-proof.html"}).status_code == 404
    )
    assert (
        next(item for item in snapshot()["contents"] if item["id"] == draft["id"])["content"]
        == changed["body"]
    )
    other = post(
        "create", {"type_id": "article", "operation_id": str(uuid4()), "fields": changed}
    ).json()["data"]
    assert write("publish", changed, other).status_code == 409
    assert (
        write(
            "publish",
            {
                **changed,
                "settings": {**changed["settings"], "display_published_at": "2099-01-01T00:00:00Z"},
            },
            draft,
        ).status_code
        == 422
    )
    old_client = {key: value for key, value in changed.items() if key != "settings"}
    saved = write("save", old_client, draft)
    assert saved.status_code == 200 and saved.json()["data"]["settings"] == draft["settings"]
    assert client.get("/v1/admin/content/editor-options").json()["data"]["terms"]
