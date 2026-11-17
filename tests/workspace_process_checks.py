"""Real API replicas: layouts, permission filtering and concurrent view accounting."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx2 as httpx

from app.modules.auth.security import hash_password


def check_workspace(
    client: Any, csrf: dict[str, str], cookies: Any, api_a: str, api_b: str, sql: Any
) -> None:
    layout_url = "/v1/admin/workspace/layout"
    analytics_url = "/v1/admin/workspace/analytics"
    assert httpx.get(api_a + "/v1/admin/workspace").status_code == 401
    initial = client.get("/v1/admin/workspace").json()["data"]["layout"]
    payload = {"revision": initial["revision"], "cards": [{"id": "core.views", "width": 6}]}
    assert client.post(layout_url, json=payload).status_code == 403
    assert client.post(analytics_url, json={"days": 7}).status_code == 403
    assert client.post(analytics_url, json={"days": 8}, headers=csrf).status_code == 422
    assert (
        client.post(
            layout_url, json={**payload, "cards": payload["cards"] * 2}, headers=csrf
        ).status_code
        == 422
    )

    def save(base: str) -> Any:
        return httpx.post(
            base + layout_url,
            json=payload,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, [api_a, api_b]))
    assert sorted(row.status_code for row in responses) == [200, 409], [
        row.text for row in responses
    ]
    saved = client.get("/v1/admin/workspace").json()["data"]["layout"]
    assert saved["cards"] == payload["cards"] and saved["revision"] == initial["revision"] + 1
    empty = client.post(layout_url, json={"revision": saved["revision"], "cards": []}, headers=csrf)
    assert empty.status_code == 200 and empty.json()["data"]["cards"] == []
    restored = client.post(
        layout_url, json={"revision": empty.json()["data"]["revision"], "cards": None}, headers=csrf
    )
    assert restored.status_code == 200 and restored.json()["data"]["cards"] is None
    identity = uuid4()
    asyncio.run(
        sql(
            "INSERT INTO contents (id,source,kind,title,path,excerpt,language,published_at,"
            "updated_at,author,reading_minutes,word_count,pinned,publication_status,"
            "visibility,content,media_urls,public_generation,access_password_hash) "
            "VALUES ($1,'test','article',"
            "'Workspace view test',$2,'','zh-CN',now(),now(),'Tester',1,0,false,"
            "'published','password','[]','{}',1,$3)",
            identity,
            "/workspace-" + str(identity),
            hash_password("test-view-only"),
        )
    )

    def view(index: int) -> Any:
        return httpx.post(
            (api_a if index % 2 else api_b) + "/v1/reader/views",
            json={"content_id": str(identity)},
            headers={"Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        visits = list(pool.map(view, range(12)))
    assert all(row.status_code == 200 for row in visits), [
        (row.status_code, row.text) for row in visits
    ]
    count = asyncio.run(
        sql(
            "SELECT c.view_count, d.views FROM contents c JOIN article_view_days d "
            "ON d.content_id=c.id WHERE c.id=$1",
            identity,
        )
    )[0]
    assert count["view_count"] == count["views"] == 12
    report = client.post(analytics_url, json={"days": 7}, headers=csrf)
    assert report.status_code == 200, report.text
    data = report.json()["data"]
    assert 1 <= len(data["daily"]) <= 7 and data["started_at"] and data["period_views"] >= 12
    assert any(row["id"] == str(identity) and row["views"] == 12 for row in data["popular"])
    assert all(
        set(row) == {"id", "action", "actor", "created_at"} for row in data["security"]["items"]
    )
    # A withdrawn article leaves public ranking immediately; totals retain their own stored truth.
    asyncio.run(sql("UPDATE contents SET publication_status='withdrawn' WHERE id=$1", identity))
    after = client.post(analytics_url, json={"days": 7}, headers=csrf).json()["data"]
    assert not any(row["id"] == str(identity) for row in after["popular"])
    assert view(0).status_code == 404
    asyncio.run(sql("DELETE FROM contents WHERE id=$1", identity))
