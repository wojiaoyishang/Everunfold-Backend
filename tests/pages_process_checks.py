"""P0: two API writers and the real durable full-content Node replica for pages."""

import asyncio
import json
import os
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2 as httpx

from tests.page_language_process_checks import check_page_languages


def check_pages(
    client: Any,
    csrf: Any,
    cookies: Any,
    api_a: str,
    api_b: str,
    sql: Any,
    state: Any,
    snapshot: Any,
    wait_for: Any,
    receiver_url: str,
    token: str,
    frontend: Path,
    backend_config: Path,
) -> None:
    prefix = "/v1/admin/pages"

    def post(value: dict[str, Any]) -> Any:
        return client.post(prefix + "/save", json=value, headers=csrf)

    def delivered() -> bool:
        return bool(state()["delivery"]) and all(
            row["status"] == "applied" for row in state()["delivery"]
        )

    # Registration is an explicit reviewed deployment step, never a browser-schema endpoint.
    raw = tomllib.loads(backend_config.read_text())
    node_id = next(iter(raw["frontends"]))
    declaration = subprocess.run(
        ["node", "--import", "tsx", "tools/export-page-manifest.mts"],
        cwd=frontend,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    registered = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.register_frontend_pages",
            "--config",
            str(backend_config),
            "--node-id",
            node_id,
            "--manifest",
            "-",
        ],
        env=os.environ.copy(),
        input=declaration.stdout,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert registered.returncode == 0, registered.stderr
    page_type = next(
        item
        for item in client.get(prefix + "/types").json()["data"]["items"]
        if item["id"] == "core.people"
    )
    assert page_type["available"] and page_type["page_count"] == 0
    registered_id = str(uuid4())
    code = {
        "id": registered_id,
        "type_id": page_type["id"],
        "expected_revision": 0,
        "operation_id": str(uuid4()),
        "apply": True,
        "value": {
            "title": "P0 People page",
            "path": "/p0-people-" + uuid4().hex,
            "enabled": True,
            "options": {
                "people": [{"name": "P0 Person", "group": "P0", "message": "Visible message"}],
                "plugin_config": {"nested": [1, False, None, {"mode": "free"}]},
            },
        },
    }
    enabled = post(code)
    assert enabled.status_code == 200, enabled.text
    wait_for(delivered)
    assert (
        next(p for p in snapshot()["pages"] if p["id"] == registered_id)["options"]["people"][0][
            "message"
        ]
        == "Visible message"
    )
    assert (
        next(p for p in snapshot()["pages"] if p["id"] == registered_id)["options"]["plugin_config"]
        == code["value"]["options"]["plugin_config"]
    )

    identity = str(uuid4())
    value = {
        "title": "P0 custom page",
        "path": "/page-" + uuid4().hex,
        "html": "<h2>Confirmed source</h2>",
        "enabled": True,
    }
    payload = {"id": identity, "expected_revision": 0, "operation_id": str(uuid4()), "value": value}
    first = post(payload)
    assert first.status_code == 200, first.text
    assert not any(p["id"] == identity for p in snapshot()["pages"])

    metadata_url = prefix + "/" + identity + "/metadata"
    metadata_payload = {"expected_revision": 0, "value": {"private": "PRIVATE PAGE METADATA"}}

    def metadata_race(base: str) -> Any:
        request = {**metadata_payload, "operation_id": str(uuid4())}
        result = httpx.patch(
            base + metadata_url,
            json=request,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=15,
        )
        return result, request

    with ThreadPoolExecutor(max_workers=2) as pool:
        metadata_results = list(pool.map(metadata_race, [api_a, api_b]))
    assert sorted(response.status_code for response, _ in metadata_results) == [200, 409]
    metadata_winner, metadata_request = next(
        (r, p) for r, p in metadata_results if r.status_code == 200
    )
    replay_metadata = httpx.patch(
        api_b + metadata_url,
        json=metadata_request,
        cookies=cookies,
        headers={**csrf, "Origin": "http://127.0.0.1:3000"},
        timeout=15,
    )
    assert replay_metadata.json() == metadata_winner.json()

    # Independent operations with the same expected revision must not overwrite each other.
    def race(base: str) -> Any:
        return httpx.post(
            base + prefix + "/save",
            json={**payload, "expected_revision": 1, "operation_id": str(uuid4()), "apply": True},
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, [api_a, api_b]))
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    applied = next(r.json()["data"] for r in results if r.status_code == 200)
    wait_for(delivered)
    assert next(p for p in snapshot()["pages"] if p["id"] == identity)["html"] == value["html"]

    private = {
        **payload,
        "operation_id": str(uuid4()),
        "expected_revision": applied["revision"],
        "value": {**value, "html": "<p>PRIVATE LATER SOURCE</p>"},
    }
    saved = post(private)
    assert saved.status_code == 200, saved.text
    retried = httpx.post(
        api_b + prefix + "/save",
        json=private,
        cookies=cookies,
        headers={**csrf, "Origin": "http://127.0.0.1:3000"},
        timeout=15,
    )
    assert retried.status_code == 200 and retried.json()["data"] == saved.json()["data"]
    # An unrelated release sends a complete snapshot, while retaining only the applied page source.
    assert (
        post(
            {
                **code,
                "operation_id": str(uuid4()),
                "expected_revision": enabled.json()["data"]["revision"],
            }
        ).status_code
        == 200
    )
    wait_for(delivered)
    assert "PRIVATE LATER SOURCE" not in json.dumps(snapshot())
    assert "PRIVATE PAGE METADATA" not in json.dumps(snapshot())
    old_revision = state()["revision"]
    event = asyncio.run(
        sql(
            "SELECT payload FROM settings_delivery_events "
            "WHERE resource='public-content' AND revision=$1",
            old_revision,
        )
    )[0]["payload"]
    removed = client.post(
        prefix + "/remove",
        json={
            "id": identity,
            "expected_revision": saved.json()["data"]["revision"],
            "operation_id": str(uuid4()),
        },
        headers=csrf,
    )
    assert removed.status_code == 200, removed.text
    wait_for(delivered)
    assert not any(p["id"] == identity for p in snapshot()["pages"])
    replay = httpx.post(
        receiver_url + "/internal/cache/v1/apply",
        content=event.encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        timeout=15,
    )
    assert replay.status_code == 200
    assert not any(p["id"] == identity for p in snapshot()["pages"])

    # Duplicate path creation is also serialized across independent API processes.
    collision_path = "/page-collision-" + uuid4().hex

    def collide(base: str) -> Any:
        return httpx.post(
            base + prefix + "/save",
            json={
                "id": str(uuid4()),
                "expected_revision": 0,
                "operation_id": str(uuid4()),
                "value": {**value, "path": collision_path},
            },
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        collisions = list(pool.map(collide, [api_a, api_b]))
    assert sorted(r.status_code for r in collisions) == [200, 409]
    check_template_release(client, csrf, api_b, cookies, state, snapshot, wait_for, delivered)
    check_page_languages(client, csrf, cookies, api_a, api_b, snapshot, wait_for, delivered)


def check_template_release(
    client: Any,
    csrf: Any,
    api_b: str,
    cookies: Any,
    state: Any,
    snapshot: Any,
    wait_for: Any,
    delivered: Any,
) -> None:
    template_url = "/v1/admin/pages/templates"
    payload = {
        "id": str(uuid4()),
        "operation_id": str(uuid4()),
        "expected_revision": 0,
        "value": {"title": "Shared P0 layout", "html": "<p>Template first</p>"},
    }
    created = client.post(template_url, json=payload, headers=csrf)
    assert created.status_code == 200, created.text
    ids = set()
    for index in range(2):
        identity = str(uuid4())
        saved = client.post(
            "/v1/admin/pages/save",
            json={
                "id": identity,
                "type_id": created.json()["data"]["type_id"],
                "operation_id": str(uuid4()),
                "expected_revision": 0,
                "value": {
                    "title": f"Template instance {index}",
                    "path": "/p0-template-" + identity,
                    "enabled": True,
                },
                "apply": True,
            },
            headers=csrf,
        )
        assert saved.status_code == 200, saved.text
        ids.add(identity)
    wait_for(delivered)
    initial = {row["id"]: row for row in snapshot()["pages"] if row["id"] in ids}
    assert len(initial) == 2 and all(
        row["html"] == "<p>Template first</p>" for row in initial.values()
    )
    revision = state()["revision"]
    updated = {
        **payload,
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "value": {**payload["value"], "html": "<p>Template changed</p>"},
    }
    accepted = httpx.post(
        api_b + template_url,
        json=updated,
        cookies=cookies,
        headers={**csrf, "Origin": "http://127.0.0.1:3000"},
        timeout=15,
    )
    assert accepted.status_code == 200, accepted.text
    retry = client.post(template_url, json=updated, headers=csrf)
    assert retry.status_code == 200 and retry.json()["data"] == accepted.json()["data"]
    wait_for(delivered)
    assert state()["revision"] == revision + 1
    replica = {row["id"]: row for row in snapshot()["pages"] if row["id"] in ids}
    assert len(replica) == 2
    for identity, row in replica.items():
        assert row["html"] == "<p>Template changed</p>"
        assert (row["path"], row["title"]) == (
            initial[identity]["path"],
            initial[identity]["title"],
        )
    assert not any(row["id"] == payload["id"] for row in snapshot()["pages"])
    template = f"{template_url}/{payload['id']}"
    assert [
        item["revision"] for item in client.get(template + "/versions").json()["data"]["items"]
    ] == [2, 1]
    restored = client.post(
        template + "/versions/restore",
        json={"revision": 1, "expected_revision": 2, "operation_id": str(uuid4())},
        headers=csrf,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["applied_revision"] == 2
    assert state()["revision"] == revision + 1
    assert "Template first" not in json.dumps(snapshot())
    impact = client.get(template + "/impact").json()["data"]
    assert {page["id"] for page in impact["pages"]} == ids
    apply = httpx.post(
        api_b + template + "/apply",
        json={"digest": impact["digest"], "expected_revision": 3, "operation_id": str(uuid4())},
        cookies=cookies,
        headers={**csrf, "Origin": "http://127.0.0.1:3000"},
        timeout=15,
    )
    assert apply.status_code == 200, apply.text
    wait_for(delivered)
    assert state()["revision"] == revision + 2
    assert all(
        row["html"] == "<p>Template first</p>" for row in snapshot()["pages"] if row["id"] in ids
    )
