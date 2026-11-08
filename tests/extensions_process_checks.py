"""Runtime activation through real replicas and the durable settings receiver."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx2 as httpx


def check_extensions(
    client: Any,
    csrf: Any,
    cookies: Any,
    api_a: str,
    api_b: str,
    wait_for: Any,
    node_id: str,
    epoch: str,
    directory: Path,
    token: str,
) -> None:
    origin = "http://127.0.0.1:3000"
    identity = str(uuid5(NAMESPACE_URL, "everunfold:pages:extension.p0"))
    inventory = {
        "protocol": "extensions-1",
        "themes": [
            {"id": "argon", "title": "Argon"},
            {"id": "paper", "title": "Paper", "parent": "argon"},
        ],
        "plugins": [
            {
                "id": "extension.p0",
                "title": "Runtime P0",
                "build": "b" * 64,
                "page_ids": ["extension.p0"],
            }
        ],
        "pages": {
            "protocol": "pages-1",
            "pages": [
                {
                    "id": "extension.p0",
                    "version": "1.0.0",
                    "title": "Extension P0",
                    "path": "/extension-p0",
                }
            ],
        },
    }
    assert httpx.get(api_a + "/v1/admin/extensions").status_code == 401
    assert client.post(f"/internal/extensions/{node_id}", json=inventory).status_code == 403

    def register(base: str) -> httpx.Response:
        return httpx.post(
            base + f"/internal/extensions/{node_id}",
            json=inventory,
            headers={"Authorization": "Bearer " + token},
            timeout=10,
        )

    assert register(api_a).status_code == 200
    deployed = client.get("/v1/admin/extensions")
    assert deployed.status_code == 200 and "no-store" in deployed.headers["cache-control"]
    assert deployed.json()["data"]["nodes"][0]["inventory"]["plugins"][0]["id"] == "extension.p0"
    before = client.get(f"/v1/admin/pages/{identity}").json()["data"]
    assert before["available"] is True
    applied_page = client.post(
        "/v1/admin/pages/save",
        json={
            "id": identity,
            "expected_revision": before["revision"],
            "operation_id": str(uuid4()),
            "value": {**before["value"], "enabled": True},
            "apply": True,
        },
        headers=csrf,
    )
    assert applied_page.status_code == 200, applied_page.text
    before = applied_page.json()["data"]

    def listed() -> bool:
        return any(
            row["id"] == identity for row in client.get("/v1/admin/pages").json()["data"]["items"]
        )

    def usage() -> None:
        result = client.post(
            "/v1/admin/extensions/usage", json={"plugin_ids": ["extension.p0"]}, headers=csrf
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["plugins"][0]["applied_pages"] == 1

    assert listed()
    usage()
    state = client.get("/v1/admin/settings").json()["data"]
    value = copy.deepcopy(state["value"])
    value["extensions"]["theme"] = "missing"
    result = client.patch(
        "/v1/admin/settings",
        json={"expected_revision": state["revision"], "value": value},
        headers=csrf,
    )
    assert result.status_code == 422 and result.json()["code"] == 42298
    assert client.get("/v1/admin/settings").json()["data"]["revision"] == state["revision"]
    value["extensions"] = {"theme": "paper", "disabled_plugins": ["extension.p0"]}
    payload = {"expected_revision": state["revision"], "value": value}

    def save(base: str) -> httpx.Response:
        return httpx.patch(
            base + "/v1/admin/settings",
            json=payload,
            cookies=cookies,
            headers={**csrf, "Origin": origin},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [api_a, api_b]))
    assert sorted(result.status_code for result in results) == [200, 409]
    after = client.get(f"/v1/admin/pages/{identity}").json()["data"]
    assert after["available"] is False and after["value"] == before["value"]
    assert not listed()
    usage()
    revision = state["revision"] + 1
    applied = directory / node_id / epoch / f"public-settings.{revision}.json"
    wait_for(applied.exists)
    assert json.loads(applied.read_text())["value"]["extensions"] == value["extensions"]
    # A fresh inventory report and a re-enable serialize on the same settings lock.
    state = client.get("/v1/admin/settings").json()["data"]
    value["extensions"]["disabled_plugins"] = []
    payload = {"expected_revision": state["revision"], "value": value}
    with ThreadPoolExecutor(max_workers=2) as pool:
        registration = pool.submit(register, api_a)
        enabling = pool.submit(save, api_b)
        assert registration.result().status_code == enabling.result().status_code == 200
    restored = client.get(f"/v1/admin/pages/{identity}").json()["data"]
    assert restored["available"] is True
    assert restored["value"] == before["value"] and restored["revision"] == before["revision"]
    assert listed()
    usage()
    wait_for(
        lambda: (
            client.get("/v1/admin/settings").json()["data"]["delivery"][0]["status"] == "applied"
        )
    )
