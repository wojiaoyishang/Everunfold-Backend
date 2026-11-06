"""Native template writes across two APIs and the durable Node settings stream."""

import asyncio
import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx


def check_shortcodes(
    client: Any,
    csrf: Any,
    cookies: Any,
    api_a: str,
    api_b: str,
    sql: Any,
    wait_for: Any,
    node_id: str,
    epoch: str,
    directory: Path,
    receiver_url: str,
    token: str,
    frontend: Path,
    node_executable: str,
    node_env: Any,
) -> None:
    def post(action: str, payload: Any, base: str | None = None) -> Any:
        if base:
            return httpx.post(
                base + "/v1/admin/shortcodes/" + action,
                json=payload,
                cookies=cookies,
                headers={**csrf, "Origin": "http://127.0.0.1:3000"},
                timeout=10,
            )
        return client.post("/v1/admin/shortcodes/" + action, json=payload, headers=csrf)

    def settings() -> Any:
        return client.get("/v1/admin/settings").json()["data"]

    original = settings()
    assert httpx.get(api_a + "/v1/admin/shortcodes").status_code == 401
    catalog = client.get("/v1/admin/shortcodes")
    assert catalog.status_code == 200, catalog.text
    assert "no-store" in catalog.headers["cache-control"]
    builtin = catalog.json()["data"]["items"][0]
    assert builtin["origin"] == "backend" and builtin["editable"] is False
    assert post("apply", {"id": builtin["id"], "expected_revision": 1}).status_code == 403
    assert (
        post(
            "save",
            {
                "id": builtin["id"],
                "expected_revision": 1,
                "value": {**builtin["source"], "name": "custom:spoof"},
            },
        ).status_code
        == 403
    )
    image = client.post(
        "/v1/admin/media/upload",
        params={"filename": "shortcode-proof.svg", "upload_id": str(uuid4())},
        content=(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20">'
            b'<rect width="20" height="20" fill="blue"/></svg>'
        ),
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )
    assert image.status_code == 200, image.text
    image = image.json()["data"]
    image_id = image["id"].removeprefix("media:")
    value: dict[str, Any] = {
        "name": "custom:p0-card",
        "title": "P0 card",
        "kind": "container",
        "parameters": [{"name": "title", "label": "Title", "default": "Default"}],
        "html": f'<section class="card"><img src="site://media/{image_id}"><h3>{{{{title}}}}</h3><div>{{{{content}}}}</div></section>',
        "css": ".card{border:1px solid currentColor;padding:1em}",
    }
    assert (
        client.post(
            "/v1/admin/shortcodes/save", json={"expected_revision": 0, "value": value}
        ).status_code
        == 403
    )
    saved = post("save", {"expected_revision": 0, "value": value})
    assert saved.status_code == 200, saved.text
    row = saved.json()["data"]
    duplicate = post("save", {"expected_revision": 0, "value": value})
    assert duplicate.status_code == 409 and duplicate.json()["code"] == 40981
    assert row["compiled"]["media_urls"][image_id] == image["url"]
    detail = client.post("/v1/admin/media/detail", json={"id": image["id"]}, headers=csrf).json()[
        "data"
    ]
    removal = client.post(
        "/v1/admin/media/remove-preview",
        json={"id": image["id"], "expected_revision": detail["revision"], "delete_original": True},
        headers=csrf,
    )
    assert removal.status_code == 409, removal.text
    assert row["revision"] == 1 and row["applied_revision"] is None
    assert settings()["revision"] == original["revision"]
    peer = httpx.get(api_b + "/v1/admin/shortcodes", cookies=cookies)
    assert any(item["id"] == row["id"] for item in peer.json()["data"]["items"])

    def race(title: str, base: str) -> Any:
        return post(
            "save",
            {"id": row["id"], "expected_revision": 1, "value": {**value, "title": title}},
            base,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(race, "API A", api_a)
        b = pool.submit(race, "API B", api_b)
        responses = [a.result(), b.result()]
    assert sorted(item.status_code for item in responses) == [200, 409]
    row = next(item.json()["data"] for item in responses if item.status_code == 200)
    invalid = copy.deepcopy(row["source"])
    invalid["css"] = "& + body {display:none}"
    preview = post("preview", {"value": invalid})
    assert preview.status_code == 200 and preview.json()["data"]["compiled"] is None
    assert preview.json()["data"]["error"]
    assert (
        post(
            "save", {"id": row["id"], "expected_revision": row["revision"], "value": invalid}
        ).status_code
        == 422
    )
    assert settings()["revision"] == original["revision"]

    action = {"id": row["id"], "expected_revision": row["revision"]}
    applied = post("apply", action)
    assert applied.status_code == 200, applied.text
    desired = settings()
    revision = desired["revision"]
    assert revision == original["revision"] + 1
    published = next(item for item in desired["value"]["shortcodes"] if item["id"] == row["id"])
    assert published["revision"] == 2 and published["scope"] in published["css"]
    counts = asyncio.run(
        sql(
            "SELECT (SELECT count(*) FROM shortcode_versions WHERE definition_id=$1) AS versions, "
            "(SELECT count(*) FROM settings_delivery_events "
            "WHERE resource='public-settings' AND revision=$2) AS events",
            UUID(row["id"]),
            revision,
        )
    )[0]
    assert counts["versions"] == 1 and counts["events"] == 1
    wait_for(lambda: settings()["delivery"][0]["status"] == "applied")
    filename = directory / node_id / epoch / f"public-settings.{revision}.json"
    committed = json.loads(filename.read_text())
    assert committed["value"]["shortcodes"] == desired["value"]["shortcodes"]

    # Fresh Node consumers read the same durable file and shared rendering code.
    for _ in range(2):
        probe = subprocess.run(
            [
                node_executable,
                "--import",
                "tsx",
                "--import",
                "./tools/extensions/node.mjs",
                "tools/fixtures/shortcodes-probe.mts",
                str(directory),
                node_id,
                epoch,
                value["name"],
            ],
            cwd=frontend,
            env=node_env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert probe.returncode == 0, probe.stderr
        rendered = json.loads(probe.stdout)
        assert rendered["revision"] == revision and not rendered["diagnostics"]
        assert (
            "From applied snapshot" in rendered["html"]
            and "<strong>P0 body</strong>" in rendered["html"]
            and "<math" in rendered["html"]
        )

    events = asyncio.run(
        sql(
            "SELECT payload FROM settings_delivery_events WHERE node_id=$1 "
            "AND resource='public-settings' AND revision <= $2 ORDER BY revision DESC LIMIT 2",
            node_id,
            revision,
        )
    )
    for event in [events[0], events[1], events[0]]:
        replay = httpx.post(
            receiver_url + "/internal/settings/apply",
            content=event["payload"].encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            timeout=8,
        )
        assert replay.status_code == 200 and replay.json()["durable"] is True
    assert json.loads(filename.read_text()) == committed

    assert post("apply", action).status_code == 200
    assert settings()["revision"] == revision
    assert (
        asyncio.run(
            sql(
                "SELECT count(*) AS count FROM shortcode_versions WHERE definition_id=$1",
                UUID(row["id"]),
            )
        )[0]["count"]
        == 1
    )
    tampered = copy.deepcopy(settings()["value"])
    tampered["shortcodes"][0]["html"] = "changed outside catalog"
    assert (
        client.patch(
            "/v1/admin/settings",
            json={"expected_revision": revision, "value": tampered},
            headers=csrf,
        ).status_code
        == 422
    )
    changed = {**row["source"], "html": row["source"]["html"].replace("<h3>", "<h3>UNAPPLIED ")}
    draft = post("save", {**action, "value": changed})
    assert draft.status_code == 200, draft.text
    row = draft.json()["data"]
    assert settings()["value"]["shortcodes"] == desired["value"]["shortcodes"]
    assert "UNAPPLIED" not in filename.read_text()
    assert (
        post(
            "save",
            {
                "id": row["id"],
                "expected_revision": row["revision"],
                "value": {**changed, "name": "custom:rename"},
            },
        ).status_code
        == 422
    )
    assert (
        post(
            "save",
            {
                "id": row["id"],
                "expected_revision": row["revision"],
                "value": {**changed, "kind": "leaf", "html": "<p>leaf</p>"},
            },
        ).status_code
        == 422
    )
    restored = post(
        "restore", {"id": row["id"], "expected_revision": row["revision"], "version": 2}
    )
    assert restored.status_code == 200, restored.text
    row = restored.json()["data"]
    assert row["revision"] == 4 and "UNAPPLIED" not in row["source"]["html"]
    archived = post("archive", {"id": row["id"], "expected_revision": row["revision"]})
    assert archived.status_code == 200, archived.text
    assert (
        next(item for item in archived.json()["data"]["items"] if item["id"] == row["id"])[
            "archived"
        ]
        is True
    )
    assert settings()["value"]["shortcodes"] == desired["value"]["shortcodes"]
    assert json.loads(filename.read_text()) == committed
