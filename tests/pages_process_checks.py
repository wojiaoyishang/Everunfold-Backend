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
            str(frontend / "registrations/pages-v1.json"),
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert registered.returncode == 0, registered.stderr
    row = next(
        r
        for r in client.get(prefix).json()["data"]["items"]
        if r["implementation_id"] == "core.people"
    )
    row = client.get(prefix + "/" + row["id"]).json()["data"]
    assert row["available"] and not row["enabled"]
    code = {
        "id": row["id"],
        "expected_revision": row["revision"],
        "operation_id": str(uuid4()),
        "apply": True,
        "value": {
            **row["value"],
            "enabled": True,
            "options": {
                "people": [{"name": "P0 Person", "group": "P0", "message": "Visible message"}]
            },
        },
    }
    enabled = post(code)
    assert enabled.status_code == 200, enabled.text
    wait_for(delivered)
    assert (
        next(p for p in snapshot()["pages"] if p["id"] == row["id"])["options"]["people"][0][
            "message"
        ]
        == "Visible message"
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
