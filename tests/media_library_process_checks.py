"""Real replicated uploads, hash reuse, reference guards and recoverable removals."""

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2 as httpx
from PIL import Image


def check_media_library(
    client: Any, csrf: dict[str, str], cookies: Any, api_a: str, api_b: str, sql: Any, uploads: Path
) -> None:
    def remove(payload: dict[str, Any]) -> Any:
        if "expected_plan" not in payload:
            preview = client.post("/v1/admin/media/remove-preview", json=payload, headers=csrf)
            if preview.status_code != 200:
                return preview
            plan = preview.json()["data"]
            payload.update(
                operation_id=plan["resume_operation_id"] or str(uuid4()),
                expected_plan=plan["token"],
                id=plan["id"],
                safe_delete=plan["safe_delete"],
                delete_original=plan["delete_original"],
            )
        return client.post("/v1/admin/media/remove", json=payload, headers=csrf)

    stream = io.BytesIO()
    Image.new("RGB", (37, 29), "orange").save(stream, "PNG")
    data = stream.getvalue()
    keys = [str(uuid4()), str(uuid4())]
    headers = {
        **csrf,
        "Content-Type": "application/octet-stream",
        "Origin": "http://127.0.0.1:3000",
    }

    def upload(job: tuple[str, str]) -> Any:
        host, key = job
        return httpx.post(
            host + "/v1/admin/media/upload",
            params={"filename": "reuse.png", "upload_id": key},
            content=data,
            headers=headers,
            cookies=cookies,
            timeout=15,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, [(api_a, keys[0]), (api_b, keys[1])]))
    assert [result.status_code for result in results] == [200, 200], [r.text for r in results]
    first, second = [response.json()["data"] for response in results]
    assert first["id"] == second["id"]
    assert sorted([first["reused"], second["reused"]]) == [False, True]
    assert upload((api_b, keys[0])).json()["data"] == first
    identity = first["id"]

    def detail() -> Any:
        return client.post("/v1/admin/media/detail", json={"id": identity}, headers=csrf).json()[
            "data"
        ]

    metadata = {**detail()["metadata"], "title": "preserved title", "alt_text": "orange proof"}
    assert (
        client.post(
            "/v1/admin/media/update",
            json={"id": identity, "expected_revision": 1, "metadata": metadata},
            headers=csrf,
        ).status_code
        == 200
    )
    promoted = client.post(
        "/v1/admin/media/upload",
        params={"filename": "icon.png", "upload_id": str(uuid4()), "purpose": "icon"},
        content=data,
        headers=headers,
    )
    assert promoted.json()["data"]["id"] == identity and promoted.json()["data"]["reused"]
    assert detail()["metadata"] == metadata
    assert identity in [
        i["id"]
        for i in client.post("/v1/admin/icons/query", json={}, headers=csrf).json()["data"]["items"]
    ]
    assert (
        client.post(
            "/v1/admin/media/upload",
            params={"filename": "reuse.png", "upload_id": keys[0]},
            content=data + b"x",
            headers=headers,
        ).status_code
        == 409
    )
    # Current settings references are protected; existing fixture already has an avatar.
    settings = client.get("/v1/admin/settings").json()["data"]
    used_id = settings["value"]["site"]["avatar_media_id"]
    used = client.post("/v1/admin/media/detail", json={"id": used_id}, headers=csrf).json()["data"]
    assert (
        remove(
            {"id": used_id, "expected_revision": used["revision"], "delete_original": True}
        ).status_code
        == 409
    )
    delete = {"id": identity, "expected_revision": detail()["revision"]}
    assert client.post("/v1/admin/media/remove", json=delete).status_code == 403
    # Failure to audit must retain both visibility and bytes.
    asyncio.run(
        sql(
            "ALTER TABLE settings_audit ADD CONSTRAINT trash_rollback "
            "CHECK (changes->>'operation' <> 'trash') NOT VALID"
        )
    )
    assert remove(delete).status_code == 503
    assert client.get(urlsplit(first["url"]).path).content == data
    asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT trash_rollback"))
    assert remove(delete).json()["data"]["deleted"]
    assert client.get(urlsplit(first["url"]).path).status_code == 404
    assert identity in [
        i["id"]
        for i in client.post("/v1/admin/media/query", json={"trash": True}, headers=csrf).json()[
            "data"
        ]["items"]
    ]
    assert upload((api_b, keys[0])).status_code == 410
    assert (
        client.post("/v1/admin/media/restore", json={"id": identity}, headers=csrf).status_code
        == 200
    )
    assert client.get(urlsplit(first["url"]).path).content == data
    # Arbitrary attachments are download-only, including active HTML and empty files.
    for name, content in [
        ("page.html", b"<script>alert(1)</script>"),
        ("empty.txt", b""),
        ("archive.zip", b"PK\x03\x04test"),
    ]:
        uploaded = client.post(
            "/v1/admin/media/upload",
            params={"filename": name, "upload_id": str(uuid4())},
            content=content,
            headers=headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        item = uploaded.json()["data"]
        assert item["kind"] == "file"
        fetched = client.get(urlsplit(item["url"]).path)
        assert (
            fetched.content == content
            and fetched.headers["content-type"] == "application/octet-stream"
        )
        assert fetched.headers["content-disposition"].startswith("attachment;")
        payload = {"id": item["id"], "expected_revision": 1, "delete_original": True}
        assert remove(payload).json()["data"]["purged"]
        assert remove(payload).json()["data"]["purged"]
        assert (
            client.post(
                "/v1/admin/media/restore", json={"id": item["id"]}, headers=csrf
            ).status_code
            == 409
        )
    # Simulate changed/missing storage; pending removal remains closed and retryable.
    row = asyncio.run(sql("SELECT object_key FROM media_assets WHERE id=$1", identity[6:]))[0]
    original = (uploads / row["object_key"]).resolve()
    assert original.is_relative_to(uploads.resolve()) and original.read_bytes() == data
    original.write_bytes(b"unexpected bytes")
    purge = {"id": identity, "expected_revision": detail()["revision"], "delete_original": True}
    assert remove(purge).status_code == 503
    assert detail()["purge_pending"] and client.get(urlsplit(first["url"]).path).status_code == 404
    original.write_bytes(data)
    result = remove(purge)
    assert result.status_code == 200 and result.json()["data"]["purged"]
    assert not original.exists()
    # A new upload after purge creates a new identity; old receipts never resurrect it.
    replacement = upload((api_a, str(uuid4()))).json()["data"]
    assert replacement["id"] != identity
    assert upload((api_b, keys[0])).status_code == 410
    source = replacement["id"]
    asyncio.run(
        sql(
            "INSERT INTO settings_audit(id,user_id,resource,revision,changes,created_at) "
            "SELECT $1,uploader_id,'public-settings',1,"
            "jsonb_build_object('before', $2::text),now() "
            "FROM media_assets WHERE id=$3",
            uuid4(),
            source,
            source[6:],
        )
    )
    assert (
        remove({"id": source, "expected_revision": 1, "delete_original": True}).status_code == 409
    )
    assert remove({"id": source, "expected_revision": 1}).status_code == 200

    from tests.media_deletion_process_checks import check_shared_removal

    check_shared_removal(client, csrf, cookies, api_a, api_b, sql, uploads)
