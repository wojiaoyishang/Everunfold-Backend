"""Real two-replica preset CAS, duplicate-name and assignment/removal races."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx2 as httpx


def check_user_presets(admin: httpx.Client, csrf: dict[str, str], api_b: str) -> None:
    api_a = str(admin.base_url).rstrip("/")
    cookies = dict(admin.cookies)
    headers = {**csrf, "Origin": "http://127.0.0.1:3000"}
    name = "P0-preset-" + uuid4().hex

    def post(base: str, path: str, value: dict[str, Any]) -> httpx.Response:
        return httpx.post(base + path, cookies=cookies, headers=headers, json=value, timeout=10)

    def create_body(label: str) -> dict[str, Any]:
        return {
            "operation_id": str(uuid4()),
            "current_password": "123456",
            "value": {"name": label, "grants": {"everunfold": {"content:read": "all"}}},
        }

    response = post(api_a, "/v1/admin/user-presets", create_body(name))
    assert response.status_code == 201, response.text
    preset = response.json()["data"]
    path = "/v1/admin/user-presets/" + preset["id"]
    bodies = [{**create_body(name + suffix), "expected_revision": 1} for suffix in ("a", "b")]

    def update(job: tuple[str, dict[str, Any]]) -> httpx.Response:
        base, body = job
        return httpx.patch(base + path, cookies=cookies, headers=headers, json=body, timeout=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, zip((api_a, api_b), bodies, strict=True)))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(i for i, result in enumerate(results) if result.status_code == 200)
    retry = update((api_b, bodies[winner]))
    assert retry.status_code == 200 and retry.json() == results[winner].json()
    duplicates = [create_body(name + "duplicate"), create_body((name + "duplicate").upper())]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda job: post(job[0], "/v1/admin/user-presets", job[1]),
                zip((api_a, api_b), duplicates, strict=True),
            )
        )
    assert sorted(r.status_code for r in results) == [201, 409]
    chosen = next(r.json()["data"] for r in results if r.status_code == 201)
    created = post(
        api_a,
        "/v1/admin/users",
        {
            "operation_id": str(uuid4()),
            "username": "preset-race-" + uuid4().hex,
            "display_name": "P0 preset",
            "password": "Preset-race-password",
            "current_password": "123456",
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()["data"]
    assignment = {
        "operation_id": str(uuid4()),
        "expected_revision": user["revision"],
        "role": "user",
        "active": True,
        "current_password": "123456",
        "preset": {"id": chosen["id"], "expected_revision": chosen["revision"]},
        "value": {"display_name": "P0 preset", "preferred_locale": "zh-CN"},
    }
    removal = {
        "operation_id": str(uuid4()),
        "expected_revision": chosen["revision"],
        "current_password": "123456",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        apply_future = pool.submit(
            lambda: httpx.patch(
                api_a + "/v1/admin/users/" + user["id"],
                cookies=cookies,
                headers=headers,
                json=assignment,
                timeout=10,
            )
        )
        remove_future = pool.submit(
            post, api_b, "/v1/admin/user-presets/" + chosen["id"] + "/remove", removal
        )
        applied, removed = apply_future.result(), remove_future.result()
    assert removed.status_code == 200 and applied.status_code in {200, 404}
    latest = admin.get("/v1/admin/users/" + user["id"]).json()["data"]
    if applied.status_code == 200:
        assert latest["permission_preset"]["id"] == chosen["id"]
        assert latest["grants"] == chosen["grants"]
    else:
        assert latest["permission_preset"] is None and latest["grants"] == {}
