"""Media checks run inside the existing two-API/real-PostgreSQL P0 fixture."""

import asyncio
import io
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2 as httpx
from PIL import Image


def check_media_management(
    client: Any, csrf: dict[str, str], cookies: Any, api_a: str, api_b: str, sql: Any
) -> None:
    stream = io.BytesIO()
    Image.new("RGB", (64, 32), "blue").save(stream, "PNG")
    original = stream.getvalue()
    uploaded = client.post(
        "/v1/admin/media/upload",
        params={"filename": "edit-proof.png", "upload_id": str(uuid4())},
        content=original,
        headers={**csrf, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200, uploaded.text
    item = uploaded.json()["data"]
    assert client.post("/v1/admin/media/detail", json={"id": item["id"]}).status_code == 403
    detail = client.post("/v1/admin/media/detail", json={"id": item["id"]}, headers=csrf).json()[
        "data"
    ]
    assert detail["width"] == 64 and detail["height"] == 32 and detail["editable"]
    metadata = {**detail["metadata"], "title": "media edit proof", "alt_text": "a blue test image"}
    update = {"id": item["id"], "expected_revision": 1, "metadata": metadata}
    asyncio.run(
        sql(
            "ALTER TABLE settings_audit ADD CONSTRAINT metadata_rollback "
            "CHECK (resource NOT LIKE 'media:%')"
        )
    )
    assert client.post("/v1/admin/media/update", json=update, headers=csrf).status_code == 503
    assert (
        client.post("/v1/admin/media/detail", json={"id": item["id"]}, headers=csrf).json()["data"][
            "revision"
        ]
        == 1
    )
    asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT metadata_rollback"))

    def update_copy(title: str) -> Any:
        return httpx.post(
            api_b + "/v1/admin/media/update",
            json={**update, "metadata": {**metadata, "title": title}},
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update_copy, ["concurrent A", "concurrent B"]))
    assert sorted(result.status_code for result in results) == [200, 409]
    current = client.post("/v1/admin/media/detail", json={"id": item["id"]}, headers=csrf).json()[
        "data"
    ]
    assert current["revision"] == 2
    assert (
        client.post(
            "/v1/admin/media/update", json={**update, "metadata": current["metadata"]}, headers=csrf
        ).json()["data"]["revision"]
        == 2
    )
    edit = {
        "id": item["id"],
        "edit_id": str(uuid4()),
        "recipe": {
            "rotation": 90,
            "crop": {"x": 0, "y": 0, "width": 32, "height": 32},
            "output_width": 16,
        },
    }
    assert client.post("/v1/admin/media/edit", json=edit).status_code == 403
    asyncio.run(
        sql(
            "ALTER TABLE settings_audit ADD CONSTRAINT edit_rollback "
            "CHECK (resource NOT LIKE 'media:%')"
        )
    )
    assert client.post("/v1/admin/media/edit", json=edit, headers=csrf).status_code == 503
    row = asyncio.run(
        sql("SELECT object_key FROM media_assets WHERE source_id = $1", item["id"][6:])
    )[0]
    assert client.get("/media/" + row["object_key"]).status_code == 404
    asyncio.run(sql("ALTER TABLE settings_audit DROP CONSTRAINT edit_rollback"))

    def retry(origin: str) -> Any:
        return httpx.post(
            origin + "/v1/admin/media/edit",
            json=edit,
            cookies=cookies,
            headers={**csrf, "Origin": "http://127.0.0.1:3000"},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(retry, [api_a, api_b]))
    assert [response.status_code for response in results] == [200, 200]
    edited = results[0].json()["data"]
    assert edited["id"] == results[1].json()["data"]["id"] != item["id"]
    assert edited["source_id"] == item["id"] and edited["width"] == edited["height"] == 16
    assert edited["metadata"] == current["metadata"]
    assert client.get(urlsplit(item["url"]).path).content == original
    with Image.open(io.BytesIO(client.get(urlsplit(edited["url"]).path).content)) as image:
        assert image.size == (16, 16)
    assert (
        client.post(
            "/v1/admin/media/edit", json={**edit, "recipe": {"rotation": 180}}, headers=csrf
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/admin/media/edit",
            json={**edit, "edit_id": str(uuid4()), "recipe": {"output_width": 100}},
            headers=csrf,
        ).status_code
        == 422
    )
    assert (
        asyncio.run(
            sql("SELECT count(*) AS count FROM settings_audit WHERE resource = $1", edited["id"])
        )[0]["count"]
        == 1
    )
