"""Scoped users, password-reset races and a live program-credential collaboration socket."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx2 as httpx
from websockets.asyncio.client import connect
from websockets.typing import Origin

from tests.avatar_process_checks import check_private_avatars

ORIGIN = "http://127.0.0.1:3000"


def check_scoped_accounts(admin: httpx.Client, csrf: dict[str, str], api_b: str) -> None:
    created = admin.post(
        "/v1/admin/users",
        headers=csrf,
        json={
            "operation_id": str(uuid4()),
            "username": "scoped_author",
            "display_name": "作者",
            "password": "Temporary-pass-12345",
            "current_password": "123456",
            "grants": {
                "everunfold": {
                    "content:read": "own",
                    "content:write": "own",
                    "shortcodes:read": "all",
                }
            },
        },
    )
    assert created.status_code == 201, created.text
    with httpx.Client(
        base_url=api_b, headers={"Origin": ORIGIN}, timeout=8, trust_env=False
    ) as member:
        initial = member.get("/v1/auth/session").json()["data"]
        logged = member.post(
            "/v1/auth/login",
            headers={"X-CSRF-Token": initial["csrf_token"]},
            json={"username": "scoped_author", "password": "Temporary-pass-12345"},
        ).json()["data"]
        current = member.get("/v1/account/profile").json()["data"]
        changed = member.post(
            "/v1/account/password",
            headers={"X-CSRF-Token": logged["csrf_token"]},
            json={
                "operation_id": str(uuid4()),
                "expected_revision": current["revision"],
                "current_password": "Temporary-pass-12345",
                "new_password": "Permanent-pass-12345",
            },
        )
        assert changed.status_code == 200
        member_csrf = {"X-CSRF-Token": changed.json()["data"]["csrf_token"]}
        access = member.get("/v1/admin/session")
        assert access.status_code == 200, access.text
        assert member.get("/v1/admin/settings").status_code == 403
        templates = member.get("/v1/admin/shortcodes")
        assert templates.status_code == 200 and "applied" in templates.json()["data"]
        prefix = "/v1/admin/content/"

        def draft(client: httpx.Client, auth: dict[str, str], title: str) -> dict[str, Any]:
            response = client.post(
                prefix + "create",
                headers=auth,
                json={
                    "operation_id": str(uuid4()),
                    "type_id": "article",
                    "fields": {
                        "title": title,
                        "body": [{"id": str(uuid4()), "type": "markdown", "content": title}],
                    },
                },
            )
            assert response.status_code == 200, response.text
            return dict(response.json()["data"])

        own = draft(member, member_csrf, "Own scoped article")
        other = draft(admin, csrf, "Other private article")
        listing = member.post(prefix + "query", headers=member_csrf, json={"type_id": "article"})
        assert listing.status_code == 200
        assert [row["id"] for row in listing.json()["data"]["items"]] == [own["id"]]
        assert (
            member.post(
                prefix + "detail", headers=member_csrf, json={"id": other["id"]}
            ).status_code
            == 404
        )
        assert (
            member.post(prefix + "publish", headers=member_csrf, json={"id": own["id"]}).status_code
            == 403
        )
        # A key has no session cookie; ticket + subsequent socket updates share the same ceiling.
        result = member.post(
            "/v1/account/api-keys",
            headers=member_csrf,
            json={
                "operation_id": str(uuid4()),
                "name": "Own editor",
                "permissions": {"content:read": "own", "content:write": "own"},
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "current_password": "Permanent-pass-12345",
            },
        )
        assert result.status_code == 201, result.text
        key = result.json()["data"]

        async def socket_revoke() -> None:
            ticket = httpx.post(
                api_b + prefix + "collaboration/ticket",
                json={"id": own["id"]},
                headers={"Authorization": "Bearer " + key["secret"]},
                trust_env=False,
            )
            assert ticket.status_code == 200, ticket.text
            uri = api_b.replace("http://", "ws://") + ticket.json()["data"]["ws_path"]
            async with connect(uri, origin=Origin(ORIGIN)) as socket:
                await socket.send(
                    json.dumps(
                        {
                            "type": "auth",
                            "ticket": ticket.json()["data"]["ticket"],
                            "client_id": 191919,
                            "state_vector": "AA==",
                        }
                    )
                )
                async with asyncio.timeout(8):
                    while json.loads(await socket.recv())["type"] != "sync":
                        pass
                revoked = member.post(
                    "/v1/account/api-keys/revoke",
                    headers=member_csrf,
                    json={"operation_id": str(uuid4()), "key_id": key["key"]["id"]},
                )
                assert revoked.status_code == 200
                async with asyncio.timeout(8):
                    while True:
                        value = json.loads(await socket.recv())
                        if value["type"] == "error":
                            assert value["code"] in {40101, 40161}
                            break

        asyncio.run(socket_revoke())
        check_private_avatars(admin, csrf, member, member_csrf, api_b)
        # Reset and old-password login race across replicas: no surviving stale session.
        current = admin.get("/v1/admin/users/" + current["id"]).json()["data"]
        racer = httpx.Client(base_url=api_b, headers={"Origin": ORIGIN}, timeout=8, trust_env=False)
        try:
            anonymous = racer.get("/v1/auth/session").json()["data"]

            def old_login() -> httpx.Response:
                return racer.post(
                    "/v1/auth/login",
                    headers={"X-CSRF-Token": anonymous["csrf_token"]},
                    json={"username": "scoped_author", "password": "Permanent-pass-12345"},
                )

            def reset() -> httpx.Response:
                return admin.post(
                    "/v1/admin/users/" + current["id"] + "/password",
                    headers=csrf,
                    json={
                        "operation_id": str(uuid4()),
                        "expected_revision": current["revision"],
                        "current_password": "123456",
                        "new_password": "Reset-password-12345",
                    },
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                login_job, reset_job = pool.submit(old_login), pool.submit(reset)
                assert reset_job.result().status_code == 200
                assert login_job.result().status_code in {200, 401}
            assert not racer.get("/v1/auth/session").json()["data"]["authenticated"]
            assert not member.get("/v1/auth/session").json()["data"]["authenticated"]
        finally:
            racer.close()
