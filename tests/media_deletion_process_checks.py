"""Exercise legacy duplicate records against real API replicas and filesystem originals."""

import asyncio
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from PIL import Image


def check_shared_removal(
    client: Any, csrf: dict[str, str], cookies: Any, api_a: str, api_b: str, sql: Any, uploads: Path
) -> None:
    def create(color: str) -> tuple[str, bytes]:
        stream = io.BytesIO()
        Image.new("RGB", (39, 33), color).save(stream, "PNG")
        data = stream.getvalue()
        result = client.post(
            "/v1/admin/media/upload",
            params={"filename": "shared.png", "upload_id": str(uuid4())},
            content=data,
            headers={**csrf, "Content-Type": "application/octet-stream"},
        )
        assert result.status_code == 200, result.text
        return result.json()["data"]["id"], data

    def path_for(identity: str) -> Path:
        row = asyncio.run(sql("SELECT object_key FROM media_assets WHERE id=$1", identity[6:]))[0]
        path = (uploads / str(row["object_key"])).resolve()
        assert path.is_relative_to(uploads.resolve())
        return path

    def clone(identity: str, data: bytes) -> str:
        # Simulate duplicates created by the pre-deduplication uploader; never touch user data.
        key = uuid4()
        object_key = f"verification/{key}/original.png"
        path = (uploads / object_key).resolve()
        assert path.is_relative_to(uploads.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        asyncio.run(
            sql(
                "INSERT INTO media_assets(id,uploader_id,purpose,filename,mime,size,"
                "sha256,storage_id,"
                "object_key,status,created_at) SELECT $1,uploader_id,purpose,filename,"
                "mime,size,sha256,"
                "storage_id,$2,'ready',now() FROM media_assets WHERE id=$3",
                key,
                object_key,
                identity[6:],
            )
        )
        return f"media:{key}"

    def prepare(identity: str, *, safe: bool = True, original: bool = True) -> dict[str, Any]:
        detail = client.post("/v1/admin/media/detail", json={"id": identity}, headers=csrf).json()[
            "data"
        ]
        payload = {
            "id": identity,
            "expected_revision": detail["revision"],
            "delete_original": original,
            "safe_delete": safe,
        }
        response = client.post("/v1/admin/media/remove-preview", json=payload, headers=csrf)
        assert response.status_code == 200, response.text
        plan = response.json()["data"]
        return {
            **payload,
            "id": plan["id"],
            "safe_delete": plan["safe_delete"],
            "delete_original": plan["delete_original"],
            "expected_plan": plan["token"],
            "operation_id": plan["resume_operation_id"] or str(uuid4()),
        }

    def remove(payload: dict[str, Any], host: str = api_a) -> Any:
        return httpx.post(
            host + "/v1/admin/media/remove",
            json=payload,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            cookies=cookies,
            timeout=15,
        )

    # Safe permanent removal hides just the selected row, retaining every original.
    first, data = create("coral")
    second = clone(first, data)
    paths = [path_for(first), path_for(second)]
    result = remove(prepare(first)).json()["data"]
    assert result["records_removed"] == 1 and result["file_retained"] and not result["purged"]
    assert all(path.read_bytes() == data for path in paths)
    trash = client.post("/v1/admin/media/query", json={"trash": True}, headers=csrf).json()["data"]
    assert first not in [item["id"] for item in trash["items"]]
    assert (
        client.post("/v1/admin/media/restore", json={"id": first}, headers=csrf).status_code == 409
    )
    final_request = prepare(second)
    final = remove(final_request, api_b).json()["data"]
    assert final["records_removed"] == 1 and final["files_deleted"] == 2 and final["purged"]
    assert not any(path.exists() for path in paths)
    # A retry after another upload cannot delete that new record.
    replacement, _ = create("coral")
    assert replacement not in [first, second]
    assert remove(final_request).json()["data"] == final
    assert path_for(replacement).read_bytes() == data

    # Trash counts as a remaining row; safe deletion must not destroy its recovery path.
    first, data = create("teal")
    second = clone(first, data)
    assert remove(prepare(second, original=False)).status_code == 200
    assert remove(prepare(first)).json()["data"]["file_retained"]
    assert (
        client.post("/v1/admin/media/restore", json={"id": second}, headers=csrf).status_code == 200
    )
    assert path_for(second).read_bytes() == data
    assert remove(prepare(second)).json()["data"]["files_deleted"] == 2

    # Non-safe physical removal is group-atomic at the database boundary.
    first, data = create("navy")
    second = clone(first, data)
    request = prepare(first, safe=False)
    asyncio.run(
        sql(
            "ALTER TABLE settings_audit ADD CONSTRAINT group_rollback "
            "CHECK (changes->>'operation' <> 'purge-requested') NOT VALID"
        )
    )
    assert remove(request).status_code == 503
    rows = asyncio.run(
        sql("SELECT deleted_at FROM media_assets WHERE sha256=$1", hashlib.sha256(data).hexdigest())
    )
    assert all(row["deleted_at"] is None for row in rows)
    assert path_for(first).exists() and path_for(second).exists()
    asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT group_rollback"))
    result = remove(request, api_b).json()["data"]
    assert result["records_removed"] == 2 and result["files_deleted"] == 2
    assert not path_for(first).exists() and not path_for(second).exists()

    # A reference on ANY duplicate blocks the group, with no partial deletion.
    first, data = create("orchid")
    second = clone(first, data)
    request = prepare(first, safe=False)
    audit_id = uuid4()
    asyncio.run(
        sql(
            "INSERT INTO settings_audit(id,user_id,resource,revision,changes,created_at) "
            "SELECT $1,uploader_id,'public-settings',1,jsonb_build_object('before',$2::text),now() "
            "FROM media_assets WHERE id=$3",
            audit_id,
            second,
            second[6:],
        )
    )
    blocked = remove(request)
    assert blocked.status_code == 409 and blocked.json()["code"] == 40937
    assert path_for(first).exists() and path_for(second).exists()

    # Two replicas cannot both observe themselves as the last safe deletion.
    first, data = create("khaki")
    second = clone(first, data)
    requests = [prepare(first), prepare(second)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda pair: remove(*pair), zip(requests, [api_a, api_b], strict=True))
        )
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    loser = [first, second][next(i for i, r in enumerate(results) if r.status_code == 409)]
    assert all(path_for(item).exists() for item in [first, second])
    assert remove(prepare(loser)).json()["data"]["files_deleted"] == 2
    assert all(not path_for(item).exists() for item in [first, second])

    # Changed group membership invalidates the preview; explicit reconfirmation sees new scope.
    first, data = create("wheat")
    request = prepare(first, safe=False)
    second = clone(first, data)
    stale = remove(request)
    assert stale.status_code == 409 and stale.json()["code"] == 40939
    assert path_for(first).exists() and path_for(second).exists()
    assert remove(prepare(first, safe=False)).json()["data"]["records_removed"] == 2
