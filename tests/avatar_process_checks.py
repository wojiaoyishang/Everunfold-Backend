"""Real owner isolation, image sanitization and competing quota reservations."""

import io
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from PIL import Image

ORIGIN = "http://127.0.0.1:3000"


def image_bytes() -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (1024, 768), tuple(uuid4().bytes[:3]))
    exif = Image.Exif()
    exif[270] = "private EXIF must be stripped"
    image.save(output, "JPEG", exif=exif)
    return output.getvalue()


def check_private_avatars(
    admin: httpx.Client,
    admin_csrf: dict[str, str],
    member: httpx.Client,
    csrf: dict[str, str],
    api_b: str,
) -> None:
    assert member.post("/v1/admin/media/query", json={}, headers=csrf).status_code == 403
    assert member.get("/v1/admin/users/avatar-policy").status_code == 403
    policy = admin.get("/v1/admin/users/avatar-policy").json()["data"]
    assert (
        policy["max_bytes"],
        policy["max_edge"],
        policy["daily_uploads"],
        policy["retained_images"],
    ) == (2097152, 512, 10, 20)
    upload_id, data = str(uuid4()), image_bytes()
    cookies = dict(member.cookies)

    def upload(job: tuple[str, str, bytes]) -> httpx.Response:
        base, identity, content = job
        return httpx.post(
            base + "/v1/account/avatars/upload",
            params={"upload_id": identity, "filename": "avatar.jpg"},
            content=content,
            cookies=cookies,
            headers={**csrf, "Origin": ORIGIN, "Content-Type": "application/octet-stream"},
            timeout=12,
        )

    api_a = str(admin.base_url).rstrip("/")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, [(api_a, upload_id, data), (api_b, upload_id, data)]))
    assert [response.status_code for response in responses] == [200, 200], [
        r.text for r in responses
    ]
    first = responses[0].json()["data"]
    assert responses[1].json()["data"]["id"] == first["id"]
    catalog = member.get("/v1/account/avatars").json()["data"]
    assert catalog["retained"] == catalog["uploaded_today"] == 1
    assert admin.get("/v1/account/avatars").json()["data"]["items"] == []
    served = httpx.get(first["url"])
    with Image.open(io.BytesIO(served.content)) as cleaned:
        assert cleaned.format == "WEBP" and max(cleaned.size) == 512 and not cleaned.getexif()
    assert upload((api_b, str(uuid4()), b"<svg></svg>")).status_code == 422
    assert upload((api_b, str(uuid4()), b"a" * 2097153)).status_code == 413
    profile = member.get("/v1/account/profile").json()["data"]
    fields = {
        name: profile[name]
        for name in ("display_name", "email", "bio", "website", "preferred_locale")
    }

    def set_avatar(
        client: httpx.Client, token: dict[str, str], owner: dict[str, Any], identity: str
    ) -> httpx.Response:
        return client.patch(
            "/v1/account/profile",
            headers=token,
            json={
                "operation_id": str(uuid4()),
                "expected_revision": owner["revision"],
                "value": {**fields, "avatar_id": identity},
            },
        )

    admin_profile = admin.get("/v1/account/profile").json()["data"]
    assert set_avatar(admin, admin_csrf, admin_profile, first["id"]).status_code == 422
    selected = set_avatar(member, csrf, profile, first["id"])
    assert selected.status_code == 200, selected.text
    assert selected.json()["data"]["avatar_url"].startswith("/media/")
    assert (
        member.post(
            "/v1/account/avatars/remove",
            headers=csrf,
            json={"operation_id": str(uuid4()), "avatar_id": first["id"]},
        ).status_code
        == 409
    )
    # One remaining slot, two API replicas: exactly one reservation wins.
    changed = admin.patch(
        "/v1/admin/users/avatar-policy",
        headers=admin_csrf,
        json={
            "operation_id": str(uuid4()),
            "expected_revision": policy["revision"],
            "current_password": "123456",
            "value": {"daily_uploads": 2, "retained_images": 2},
        },
    )
    assert changed.status_code == 200, changed.text
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                upload, [(api_a, str(uuid4()), image_bytes()), (api_b, str(uuid4()), image_bytes())]
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    second = next(r.json()["data"] for r in responses if r.status_code == 200)
    removed = member.post(
        "/v1/account/avatars/remove",
        headers=csrf,
        json={"operation_id": str(uuid4()), "avatar_id": second["id"]},
    )
    assert removed.status_code == 200
    assert upload((api_b, str(uuid4()), image_bytes())).status_code == 429
    assert upload((api_b, upload_id, data)).status_code == 200
    assert member.get("/v1/account/avatars").json()["data"]["uploaded_today"] == 2
