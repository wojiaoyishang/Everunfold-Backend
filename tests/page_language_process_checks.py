"""P0: independent API processes cannot double-toggle or double-create one page language."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx2 as httpx


def check_page_languages(
    client: Any,
    csrf: Any,
    cookies: Any,
    api_a: str,
    api_b: str,
    snapshot: Any,
    wait_for: Any,
    delivered: Any,
) -> None:
    prefix = "/v1/admin/pages/"
    headers = {**csrf, "Origin": "http://127.0.0.1:3000"}
    identity = str(uuid4())
    value: dict[str, Any] = {
        "title": "P0 language page",
        "path": "/page-language-" + uuid4().hex,
        "html": "<p>Applied original</p>",
        "enabled": True,
    }
    created = client.post(
        prefix + "save",
        headers=csrf,
        json={
            "id": identity,
            "expected_revision": 0,
            "value": value,
            "apply": True,
            "operation_id": str(uuid4()),
        },
    )
    assert created.status_code == 200, created.text
    saved = client.post(
        prefix + "save",
        headers=csrf,
        json={
            "id": identity,
            "expected_revision": created.json()["data"]["revision"],
            "operation_id": str(uuid4()),
            "value": {**value, "html": "<p>PRIVATE PAGE LANGUAGE DRAFT</p>"},
        },
    )
    assert saved.status_code == 200, saved.text

    def race(path: str, payload: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
        def run(base: str) -> tuple[Any, dict[str, Any]]:
            request = {**payload, "operation_id": str(uuid4())}
            return httpx.post(
                base + prefix + path, json=request, headers=headers, cookies=cookies, timeout=15
            ), request

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(run, [api_a, api_b]))
        assert sorted(response.status_code for response, _ in responses) == [200, 409], [
            r.text for r, _ in responses
        ]
        result, request = next((r, p) for r, p in responses if r.status_code == 200)
        replay = httpx.post(
            api_b + prefix + path, json=request, headers=headers, cookies=cookies, timeout=15
        )
        assert replay.json() == result.json()
        return responses

    responses = race(
        "enabled",
        {"id": identity, "expected_revision": saved.json()["data"]["revision"], "enabled": False},
    )
    off = next(r.json()["data"] for r, _ in responses if r.status_code == 200)
    wait_for(delivered)
    assert not any(p["id"] == identity for p in snapshot()["pages"])
    on = client.post(
        prefix + "enabled",
        json={
            "id": identity,
            "expected_revision": off["revision"],
            "enabled": True,
            "operation_id": str(uuid4()),
        },
        headers=csrf,
    )
    assert on.status_code == 200, on.text
    wait_for(delivered)
    assert next(p for p in snapshot()["pages"] if p["id"] == identity)["html"] == value["html"]
    responses = race(
        "translations/create",
        {"id": identity, "expected_revision": 1, "language": "en", "copy_source": True},
    )
    group = next(r.json()["data"] for r, _ in responses if r.status_code == 200)
    assert len(group["members"]) == 2
    translated = next(m for m in group["members"] if not m["is_original"])
    assert translated["path"].startswith("/en/") and not translated["enabled"]
    directory = client.get(prefix.rstrip("/")).json()["data"]["items"]
    assert identity in {r["id"] for r in directory} and translated["id"] not in {
        r["id"] for r in directory
    }
    assert "PRIVATE PAGE LANGUAGE DRAFT" not in str(snapshot())
    # A retained source cannot take a public path that was claimed while disabled.
    latest = on.json()["data"]
    moved = client.post(
        prefix + "save",
        json={
            "id": identity,
            "expected_revision": latest["revision"],
            "operation_id": str(uuid4()),
            "value": {**latest["value"], "path": value["path"] + "-draft"},
        },
        headers=csrf,
    )
    off = client.post(
        prefix + "enabled",
        json={
            "id": identity,
            "expected_revision": moved.json()["data"]["revision"],
            "operation_id": str(uuid4()),
            "enabled": False,
        },
        headers=csrf,
    ).json()["data"]
    assert (
        client.post(
            prefix + "save",
            json={
                "id": str(uuid4()),
                "expected_revision": 0,
                "operation_id": str(uuid4()),
                "value": {**value, "title": "P0 path claimant"},
            },
            headers=csrf,
        ).status_code
        == 200
    )
    collision = client.post(
        prefix + "enabled",
        json={
            "id": identity,
            "expected_revision": off["revision"],
            "operation_id": str(uuid4()),
            "enabled": True,
        },
        headers=csrf,
    )
    assert collision.status_code == 409, collision.text
