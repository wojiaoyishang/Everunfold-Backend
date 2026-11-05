"""Two real API processes, grouped pagination and independent public revisions."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx2 as httpx


def check_translations(
    client: Any, csrf: dict[str, str], cookies: Any, api_a: str, api_b: str
) -> None:
    prefix = "/v1/admin/content/"

    def post(path: str, data: dict[str, Any]) -> Any:
        return client.post(prefix + path, json=data, headers=csrf)

    assert (
        httpx.post(api_a + prefix + "translations/query", json={"id": str(uuid4())}).status_code
        == 401
    )
    fields = {
        "title": "Language group transaction proof",
        "body": [{"id": str(uuid4()), "type": "markdown", "content": "独立中文正文"}],
    }
    original = post(
        "create", {"operation_id": str(uuid4()), "type_id": "article", "fields": fields}
    ).json()["data"]
    identity = original["id"]
    request = {
        "id": identity,
        "expected_revision": 1,
        "language": "en",
        "copy_source": True,
        "operation_id": str(uuid4()),
    }
    competing = {**request, "operation_id": str(uuid4())}

    def concurrent(job: tuple[str, dict[str, Any]]) -> Any:
        base, payload = job
        return httpx.post(
            base + prefix + "translations/create",
            json=payload,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(concurrent, [(api_a, request), (api_b, competing)]))
    assert sorted(result.status_code for result in results) == [200, 409], [
        result.text for result in results
    ]
    winner = request if results[0].status_code == 200 else competing
    group = next(result.json()["data"] for result in results if result.status_code == 200)
    assert post("translations/create", winner).json()["data"] == group
    translated = next(item for item in group["members"] if item["language"] == "en")
    target = post("detail", {"id": translated["id"]}).json()["data"]
    assert target["body"][0]["id"] != original["body"][0]["id"]
    assert target["body"][0]["content"] == original["body"][0]["content"]
    assert target["settings"]["path"].startswith("/en/")
    english_fields = {
        "title": "English-only translation needle",
        "body": [
            {
                "id": target["body"][0]["id"],
                "type": "markdown",
                "content": "Independent English version",
            }
        ],
        "settings": target["settings"],
    }
    saved = post(
        "save",
        {
            "id": target["id"],
            "expected_revision": target["revision"],
            "operation_id": str(uuid4()),
            "fields": english_fields,
        },
    )
    assert saved.status_code == 200, saved.text
    target = saved.json()["data"]
    found = post(
        "query", {"type_id": "article", "search": "English-only translation needle", "page_size": 1}
    ).json()["data"]
    assert found["total"] == 1 and len(found["items"]) == 1
    assert found["items"][0]["id"] == identity and len(found["items"][0]["translations"]) == 2
    assert (
        post(
            "query",
            {"type_id": "article", "language": "en", "search": "Language group transaction proof"},
        ).json()["data"]["total"]
        == 0
    )
    assert post("detail", {"id": identity}).json()["data"]["body"] == original["body"]
    for item, content in ((original, fields), (target, english_fields)):
        result = post(
            "publish",
            {
                "id": item["id"],
                "expected_revision": item["revision"],
                "operation_id": str(uuid4()),
                "fields": content,
            },
        )
        assert result.status_code == 200, result.text
    public = client.get("/v1/contents/resolve", params={"path": target["settings"]["path"]}).json()[
        "data"
    ]
    assert public["language"] == "en" and len(public["translations"]) == 2
    assert all(
        set(item) == {"id", "language", "path", "access", "is_original"}
        for item in public["translations"]
    )
    assert (
        client.get(
            "/v1/contents", params={"q": "English-only translation needle", "language": "zh-CN"}
        ).json()["data"]["total"]
        == 0
    )
    assert (
        client.get(
            "/v1/contents", params={"q": "English-only translation needle", "language": "en"}
        ).json()["data"]["total"]
        == 1
    )
    group = post("translations/query", {"id": identity}).json()["data"]
    detached = post(
        "translations/unlink",
        {"id": target["id"], "operation_id": str(uuid4()), "expected_revision": group["revision"]},
    )
    assert detached.status_code == 200, detached.text
    group = detached.json()["data"]
    assert len(group["members"]) == 1
    relinked = post(
        "translations/link",
        {
            "id": identity,
            "target_id": target["id"],
            "operation_id": str(uuid4()),
            "expected_revision": group["revision"],
        },
    )
    assert relinked.status_code == 200, relinked.text
    group = relinked.json()["data"]
    mutation = {
        "id": identity,
        "operation_id": str(uuid4()),
        "expected_revision": group["revision"],
        "members": {item["id"]: item["revision"] for item in group["members"]},
    }
    archived = post("translations/trash", mutation)
    assert archived.status_code == 200, archived.text
    assert post("translations/trash", mutation).json()["data"] == archived.json()["data"]
    assert all(item["status"] == "trash" for item in archived.json()["data"]["members"])
    assert client.get("/v1/contents/resolve", params={"path": public["path"]}).status_code == 404
