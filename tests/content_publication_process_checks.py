"""Real replicas, leased Worker delivery, receiver restart and public withdrawal."""

import asyncio
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx

from tests.imported_preview_process_checks import check_imported_preview


def check_content_publication(
    client: Any,
    csrf: Any,
    cookies: Any,
    api_a: str,
    api_b: str,
    sql: Any,
    wait_for: Any,
    receiver: Any,
    restart: Any,
    receiver_url: str,
    token: str,
    frontend: Path,
    node: str,
    node_env: dict[str, str],
    backend_config: Path,
) -> None:
    prefix = "/v1/admin/content/"

    def post(action: str, value: Any = None) -> Any:
        return client.post(prefix + action, json=value, headers=csrf)

    fields: dict[str, Any] = {
        "title": "Published process proof",
        "body": [{"id": str(uuid4()), "type": "markdown", "content": "Visible published text"}],
    }
    draft = post(
        "create", {"type_id": "article", "operation_id": str(uuid4()), "fields": fields}
    ).json()["data"]
    payload = {
        "id": draft["id"],
        "expected_revision": draft["revision"],
        "operation_id": str(uuid4()),
        "fields": fields,
    }
    before = client.get(prefix + "publication").json()["data"]["revision"]
    asyncio.run(
        sql(
            "ALTER TABLE content_draft_revisions ADD CONSTRAINT publication_audit_failure "
            "CHECK (action <> 'publish')"
        )
    )
    rejected = post("publish", payload)
    assert rejected.status_code == 503, rejected.text
    assert not asyncio.run(sql("SELECT id FROM contents WHERE id=$1", UUID(draft["id"])))
    assert client.get(prefix + "publication").json()["data"]["revision"] == before
    asyncio.run(
        sql("ALTER TABLE content_draft_revisions DROP CONSTRAINT publication_audit_failure")
    )
    receiver.terminate()
    receiver.wait(timeout=8)

    def publish(base: str) -> Any:
        return httpx.post(
            base + prefix + "publish",
            json=payload,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, [api_a, api_b]))
    assert [item.status_code for item in results] == [200, 200], [r.text for r in results]
    published = results[0].json()["data"]
    assert published == results[1].json()["data"]
    assert published["published_revision"] == published["revision"]
    assert (
        client.get("/v1/contents/resolve", params={"path": published["public_path"]}).status_code
        == 200
    )

    def state() -> Any:
        return client.get(prefix + "publication").json()["data"]

    wait_for(lambda: state()["delivery"][0]["status"] == "failed")
    assert state()["revision"] == before + 1
    restart()
    assert post("sync").status_code == 200
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")

    def snapshot() -> Any:
        result = subprocess.run(
            [node, "--import", "tsx", "tools/fixtures/content-snapshot.mts"],
            cwd=frontend,
            env=node_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    visible = next(item for item in snapshot()["contents"] if item["id"] == draft["id"])
    assert visible["content"] == fields["body"]
    # Saving a later private draft cannot leak into the published version.
    changed = {**fields, "body": [{**fields["body"][0], "content": "Private unfinished edit"}]}
    saved = post(
        "save",
        {
            "id": draft["id"],
            "operation_id": str(uuid4()),
            "expected_revision": published["revision"],
            "fields": changed,
        },
    ).json()["data"]
    assert state()["revision"] == before + 1
    assert "Private unfinished" not in json.dumps(snapshot())
    assert post("publish", {**payload, "operation_id": str(uuid4())}).status_code == 409
    old_event = asyncio.run(
        sql(
            "SELECT payload FROM settings_delivery_events "
            "WHERE resource='public-content' AND revision=$1",
            before + 1,
        )
    )[0]["payload"]
    withdrawn = post(
        "withdraw",
        {"id": draft["id"], "operation_id": str(uuid4()), "expected_revision": saved["revision"]},
    )
    assert withdrawn.status_code == 200, withdrawn.text
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    assert (
        client.get("/v1/contents/resolve", params={"path": published["public_path"]}).status_code
        == 404
    )
    assert not any(item["id"] == draft["id"] for item in snapshot()["contents"])
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    replay = httpx.post(
        receiver_url + "/internal/cache/v1/apply", content=old_event.encode(), headers=headers
    )
    assert replay.status_code == 200
    assert not any(item["id"] == draft["id"] for item in snapshot()["contents"])
    denied = httpx.post(
        receiver_url + "/internal/cache/v1/apply",
        content=old_event.encode(),
        headers={"X-Forwarded-For": "127.0.0.1", "Authorization": "Bearer wrong"},
    )
    assert denied.status_code == 403
    # Real no-title shuoshuo publication, then trash performs withdrawal; restore remains private.
    moment = post(
        "create",
        {"type_id": "shuoshuo", "operation_id": str(uuid4()), "fields": {"body": fields["body"]}},
    ).json()["data"]
    moment = post(
        "publish",
        {
            "id": moment["id"],
            "operation_id": str(uuid4()),
            "expected_revision": moment["revision"],
            "fields": {"body": fields["body"]},
        },
    ).json()["data"]
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    assert (
        next(item for item in snapshot()["contents"] if item["id"] == moment["id"])["kind"]
        == "shuoshuo"
    )
    trashed = post(
        "trash",
        {"id": moment["id"], "operation_id": str(uuid4()), "expected_revision": moment["revision"]},
    ).json()["data"]
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    restored = post(
        "restore",
        {
            "id": moment["id"],
            "operation_id": str(uuid4()),
            "expected_revision": trashed["revision"],
        },
    ).json()["data"]
    assert restored["published_revision"] is None
    assert not any(item["id"] == moment["id"] for item in snapshot()["contents"])
    native = post(
        "publish",
        {
            "id": draft["id"],
            "operation_id": str(uuid4()),
            "expected_revision": withdrawn.json()["data"]["revision"],
            "fields": fields,
        },
    )
    assert native.status_code == 200, native.text
    check_imported_preview(client, sql, state, snapshot, wait_for, backend_config, draft["id"])
