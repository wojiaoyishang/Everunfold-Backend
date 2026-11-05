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

    def localized_health(index: int) -> None:
        language = "en" if index % 2 else "zh-CN"
        response = httpx.get(
            (api_a if index % 3 else api_b) + "/health/live", headers={"X-Site-Locale": language}
        )
        assert response.status_code == 200
        assert response.headers["content-language"] == language
        assert response.json()["msg"] == ("Request successful" if language == "en" else "请求成功")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(localized_health, range(24)))

    assert (
        httpx.post(
            api_a + prefix + "translations/query",
            json={"id": str(uuid4())},
            headers={"Origin": "http://127.0.0.1:3000"},
        ).status_code
        == 401
    )
    fields = {
        "title": "Language group transaction proof",
        "settings": {"categories": ["语言分组验收"]},
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
    removed = post(
        "trash",
        {"id": target["id"], "expected_revision": target["revision"], "operation_id": str(uuid4())},
    )
    assert removed.status_code == 200, removed.text
    assert (
        post("query", {"type_id": "article", "search": fields["title"], "status": "trash"}).json()[
            "data"
        ]["total"]
        == 0
    )
    restored = post(
        "restore",
        {
            "id": target["id"],
            "expected_revision": removed.json()["data"]["revision"],
            "operation_id": str(uuid4()),
        },
    )
    assert restored.status_code == 200, restored.text
    target = restored.json()["data"]
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
    terms = client.get("/v1/taxonomies", params={"kind": "category"}).json()["data"]["items"]
    assert next(item["count"] for item in terms if item["name"] == "语言分组验收") == 1
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
    state = client.get("/v1/admin/settings").json()["data"]
    settings_value = state["value"]
    settings_value["site"]["language_catalog"]["languages"].append(
        {"code": "ja", "name": "日本語", "prefix": "/ja", "enabled": True, "ui_locale": "en"}
    )
    configured = client.patch(
        "/v1/admin/settings",
        json={"expected_revision": state["revision"], "value": settings_value},
        headers=csrf,
    )
    assert configured.status_code == 200, configured.text
    assert any(
        item["code"] == "ja" for item in client.get("/v1/languages").json()["data"]["languages"]
    )
    third = post(
        "translations/create",
        {
            "id": identity,
            "expected_revision": group["revision"],
            "operation_id": str(uuid4()),
            "language": "ja",
            "copy_source": True,
        },
    )
    assert third.status_code == 200, third.text
    group = third.json()["data"]
    assert len(group["members"]) == 3
    japanese = next(item for item in group["members"] if item["language"] == "ja")
    assert (
        post("detail", {"id": japanese["id"]}).json()["data"]["settings"]["path"].startswith("/ja/")
    )
    listed = post(
        "query", {"type_id": "article", "language": "ja", "search": fields["title"]}
    ).json()["data"]
    assert listed["total"] == 1 and listed["items"][0]["id"] == identity
    state = configured.json()["data"]
    settings_value = state["value"]
    settings_value["site"]["language_catalog"]["languages"] = [
        item
        for item in settings_value["site"]["language_catalog"]["languages"]
        if item["code"] != "ja"
    ]
    rejected = client.patch(
        "/v1/admin/settings",
        json={"expected_revision": state["revision"], "value": settings_value},
        headers=csrf,
    )
    assert rejected.status_code == 422 and rejected.json()["code"] == 42292
    settings_value = state["value"]
    settings_value["site"]["language_catalog"]["languages"].append(
        {"code": "ja", "name": "日本語", "prefix": "/ja", "enabled": False, "ui_locale": "en"}
    )
    disabled = client.patch(
        "/v1/admin/settings",
        json={"expected_revision": state["revision"], "value": settings_value},
        headers=csrf,
    )
    assert disabled.status_code == 200, disabled.text
    assert (
        post(
            "create",
            {
                "operation_id": str(uuid4()),
                "type_id": "article",
                "language": "ja",
                "fields": {"title": "Blocked disabled language", "body": []},
            },
        ).status_code
        == 422
    )
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
