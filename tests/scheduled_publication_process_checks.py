"""Two APIs, two real Workers and a Scheduler exercise immutable one-shot publication runs."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx


def check_scheduled_publication(
    client: Any,
    csrf: Any,
    cookies: Any,
    api_a: str,
    api_b: str,
    sql: Any,
    wait_for: Any,
    worker: Any,
    restart_worker: Any,
) -> None:
    prefix = "/v1/admin/content/"

    def post(action: str, value: Any) -> Any:
        response = client.post(prefix + action, json=value, headers=csrf)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def create(path: str) -> tuple[Any, Any]:
        fields = {
            "title": "Scheduled publication proof",
            "body": [{"id": str(uuid4()), "type": "markdown", "content": "Frozen public text"}],
            "settings": {"path": path},
        }
        return post(
            "create", {"type_id": "article", "operation_id": str(uuid4()), "fields": fields}
        ), fields

    def command(row: Any, fields: Any, seconds: float = 60) -> Any:
        return {
            "id": row["id"],
            "expected_revision": row["revision"],
            "operation_id": str(uuid4()),
            "fields": fields,
            "scheduled_for": (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
        }

    def read(row: Any) -> Any:
        return post("detail", {"id": row["id"]})

    def due(row: Any) -> None:
        asyncio.run(
            sql(
                "UPDATE content_drafts SET scheduled_for=now()-interval '1 second' WHERE id=$1",
                UUID(row["id"]),
            )
        )

    # A request persisted before downtime remains executable after expired queue ticks.
    worker.terminate()
    worker.wait(timeout=8)
    row, fields = create("/scheduled-restart.html")
    request = command(row, fields, 2)

    def arrange(base: str) -> Any:
        return httpx.post(
            base + prefix + "schedule",
            json=request,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(arrange, [api_a, api_b]))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    queued = responses[0].json()["data"]
    assert queued == responses[1].json()["data"]
    assert queued["scheduled_revision"] == queued["revision"] == 2
    assert queued["published_revision"] is None
    assert "scheduled_payload" not in queued and "access_password_hash" not in queued
    private = {**fields, "body": [{**fields["body"][0], "content": "Later private working text"}]}
    saved = post(
        "save",
        {"id": row["id"], "expected_revision": 2, "operation_id": str(uuid4()), "fields": private},
    )
    time.sleep(2.2)
    assert read(row)["published_revision"] is None
    assert not asyncio.run(sql("SELECT id FROM contents WHERE id=$1", UUID(row["id"])))
    restart_worker()
    restart_worker()
    wait_for(lambda: read(row)["published_revision"] == 2)
    latest = read(row)
    assert latest["revision"] == saved["revision"] == 3
    assert latest["body"] == private["body"] and latest["scheduled_for"] is None
    content = client.get(
        "/v1/contents/resolve", params={"path": fields["settings"]["path"]}
    ).json()["data"]
    assert content["content"] == fields["body"]
    assert (
        asyncio.run(
            sql(
                "SELECT count(*) AS n FROM settings_audit WHERE resource='content-publication' "
                "AND changes->>'draft_id'=$1",
                row["id"],
            )
        )[0]["n"]
        == 1
    )
    assert (
        asyncio.run(sql("SELECT public_generation FROM contents WHERE id=$1", UUID(row["id"])))[0][
            "public_generation"
        ]
        == 1
    )

    # Explicit cancellation and trash/restore remove registered runs; no delayed resurrection.
    cancelled, value = create("/scheduled-cancel.html")
    cancelled = post("schedule", command(cancelled, value))
    cancel = {
        "id": cancelled["id"],
        "expected_revision": cancelled["revision"],
        "operation_id": str(uuid4()),
    }
    result = post("cancel-schedule", cancel)
    assert post("cancel-schedule", cancel) == result and result["scheduled_for"] is None
    trashed = post("schedule", command(result, value))
    trashed = post(
        "trash",
        {
            "id": trashed["id"],
            "expected_revision": trashed["revision"],
            "operation_id": str(uuid4()),
        },
    )
    restored = post(
        "restore",
        {
            "id": trashed["id"],
            "expected_revision": trashed["revision"],
            "operation_id": str(uuid4()),
        },
    )
    assert restored["scheduled_for"] is None and restored["published_revision"] is None

    # Merging must freeze publication, while another unrelated due article can still run.
    merging, value = create("/scheduled-merge.html")
    merging = post("schedule", command(merging, value))
    post("collaboration/ticket", {"id": merging["id"], "source_first": True})
    branch_id, merge_id = str(uuid4()), str(uuid4())
    post(
        "versions/branch/upload",
        {
            "id": merging["id"],
            "branch_id": branch_id,
            "base_revision": merging["revision"],
            "fields": value,
        },
    )
    post(
        "versions/merge/begin", {"id": merging["id"], "branch_id": branch_id, "merge_id": merge_id}
    )
    due(merging)
    other, other_fields = create("/scheduled-parallel.html")
    other = post("schedule", command(other, other_fields, 1))
    wait_for(lambda: read(other)["published_revision"] is not None)
    assert read(merging)["published_revision"] is None and read(merging)["scheduled_for"]
    post("versions/merge/cancel", {"id": merging["id"], "merge_id": merge_id})
    wait_for(lambda: read(merging)["published_revision"] is not None)

    # Recheck address ownership at execution; an occupied address never replaces its owner.
    collision, collision_fields = create("/scheduled-collision.html")
    collision = post("schedule", command(collision, collision_fields))
    owner, owner_fields = create("/scheduled-collision.html")
    owner = post(
        "publish", {k: v for k, v in command(owner, owner_fields).items() if k != "scheduled_for"}
    )
    due(collision)
    wait_for(lambda: read(collision)["schedule_error"] == "path_conflict")
    assert read(collision)["published_revision"] is None
    assert (
        client.get("/v1/contents/resolve", params={"path": "/scheduled-collision.html"}).json()[
            "data"
        ]["id"]
        == owner["id"]
    )

    # Revoked account permissions cannot be converted into an unattended future publish.
    revoked, value = create("/scheduled-revoked.html")
    revoked = post("schedule", command(revoked, value))
    actor = UUID(revoked["owner_id"])
    try:
        asyncio.run(sql("UPDATE users SET active=false WHERE id=$1", actor))
        due(revoked)
        wait_for(
            lambda: (
                asyncio.run(
                    sql(
                        "SELECT schedule_error FROM content_drafts WHERE id=$1", UUID(revoked["id"])
                    )
                )[0]["schedule_error"]
                == "permission_changed"
            )
        )
    finally:
        asyncio.run(sql("UPDATE users SET active=true WHERE id=$1", actor))
    assert read(revoked)["published_revision"] is None

    # Commit failure rolls back Content and Outbox together and leaves the run retryable.
    retry, value = create("/scheduled-transaction.html")
    retry = post("schedule", command(retry, value))
    asyncio.run(
        sql(
            "ALTER TABLE settings_audit ADD CONSTRAINT schedule_commit_failure "
            "CHECK (resource <> 'content-publication')"
        )
    )
    try:
        due(retry)
        time.sleep(2.2)
        assert not asyncio.run(sql("SELECT id FROM contents WHERE id=$1", UUID(retry["id"])))
        assert read(retry)["scheduled_for"]
    finally:
        asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT schedule_commit_failure"))
    wait_for(lambda: read(retry)["published_revision"] is not None)
    assert (
        asyncio.run(
            sql(
                "SELECT count(*) AS n FROM settings_audit WHERE resource='content-publication' "
                "AND changes->>'draft_id'=$1",
                retry["id"],
            )
        )[0]["n"]
        == 1
    )
