"""P0: explicit machine grants, replay boundaries, budgets and revocation fencing."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx2 as httpx
from sqlalchemy.engine import make_url

from app.core.config import Settings


def check_captcha_keys(
    client: Any,
    csrf: Any,
    api_b: str,
    sql: Any,
    patch: Any,
    rules: Any,
    article: Any,
    page_id: str,
    settings: Settings,
    answer: Any,
) -> None:
    def key(permissions: dict[str, str]) -> Any:
        response = client.post(
            "/v1/account/api-keys",
            headers=csrf,
            json={
                "operation_id": str(uuid4()),
                "name": "CAPTCHA P0 " + uuid4().hex,
                "permissions": permissions,
                "current_password": "123456",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 201, response.status_code
        return response.json()["data"]

    def post(path: str, body: Any, token: str) -> Any:
        response = httpx.post(
            api_b + path,
            json=body,
            headers={"Authorization": "Bearer " + token},
            trust_env=False,
            timeout=12,
        )
        assert "no-store" in response.headers["cache-control"]
        return response

    bypass = {"captcha:read_bypass": "all", "captcha:comment_bypass": "all"}
    plain, machine = key({"content:read": "all"}), key(bypass)
    target = {"id": article["id"]}
    assert post("/v1/reader/access", target, plain["secret"]).status_code == 403
    assert post("/v1/reader/access", target, "efk_invalid").status_code == 401
    assert post("/v1/reader/access", target, machine["secret"]).status_code == 200
    assert post("/v1/reader/access-page", {"id": page_id}, machine["secret"]).status_code == 200
    challenge = {"action": "read", "content_id": article["id"], "client_id": str(uuid4())}
    assert not post("/v1/reader/captcha/challenge", challenge, machine["secret"]).json()["data"][
        "required"
    ]
    assert (
        post(
            "/v1/reader/captcha/challenge",
            {"action": "login", "client_id": str(uuid4())},
            machine["secret"],
        ).status_code
        == 403
    )
    # An anonymous proof is not transferable to a different credential.
    anonymous_proof = answer("read", article["id"])
    assert (
        post(
            "/v1/reader/access", {**target, "captcha": anonymous_proof}, plain["secret"]
        ).status_code
        == 403
    )

    def read(_: int) -> Any:
        return post("/v1/reader/access", target, machine["secret"])

    with ThreadPoolExecutor(4) as pool:
        assert all(response.status_code == 200 for response in pool.map(read, range(4)))
    unlocked = post("/v1/reader/access", target, machine["secret"]).json()["data"]
    submitted = {
        "operation_id": str(uuid4()),
        "content_id": article["id"],
        "client_id": str(uuid4()),
        "author": "P0 machine",
        "body": "Pending machine comment",
        "read_receipt": unlocked["read_receipt"],
    }
    assert post("/v1/reader/comments", submitted, machine["secret"]).status_code == 200
    assert post("/v1/reader/comments", submitted, machine["secret"]).status_code == 200
    assert post("/v1/reader/comments", submitted, "efk_invalid").status_code == 401
    other = key(bypass)
    assert (
        post(
            "/v1/reader/comments", {**submitted, "operation_id": str(uuid4())}, other["secret"]
        ).json()["code"]
        == 40388
    )

    patch(rules, api_key_bypass=False)
    assert post("/v1/reader/access", target, machine["secret"]).json()["code"] == 40386
    patch(rules, api_key_bypass=True)
    # A policy change does not enlarge an existing Key's explicit grant.
    assert post("/v1/reader/access", target, plain["secret"]).status_code == 403

    fields = {
        "title": "CAPTCHA password boundary",
        "body": [{"id": str(uuid4()), "type": "markdown", "content": "Password still applies"}],
        "settings": {"visibility": "password"},
        "access_password": "P0-content-password",
    }
    draft = client.post(
        "/v1/admin/content/create",
        json={"type_id": "article", "operation_id": str(uuid4()), "fields": fields},
        headers=csrf,
    ).json()["data"]
    published = client.post(
        "/v1/admin/content/publish",
        json={
            "id": draft["id"],
            "expected_revision": draft["revision"],
            "operation_id": str(uuid4()),
            "fields": fields,
        },
        headers=csrf,
    )
    assert published.status_code == 200, published.text
    assert post("/v1/reader/access", {"id": draft["id"]}, machine["secret"]).json()["code"] == 40350
    assert (
        post(
            "/v1/reader/access",
            {"id": draft["id"], "password": "P0-content-password"},
            machine["secret"],
        ).status_code
        == 200
    )
    limited = key(bypass)
    patch(rules, api_key_rate_limit=2)
    assert post("/v1/reader/access", target, limited["secret"]).status_code == 200
    assert post("/v1/reader/access", target, limited["secret"]).status_code == 200
    throttled = post("/v1/reader/access", target, limited["secret"])
    assert throttled.status_code == 429 and throttled.headers["Retry-After"] == "60"
    patch(rules, api_key_rate_limit=60)

    machine_id = UUID(machine["key"]["id"])
    asyncio.run(sql("UPDATE api_keys SET audience='other-project' WHERE id=$1", machine_id))
    assert post("/v1/reader/access", target, machine["secret"]).status_code == 401
    asyncio.run(
        sql(
            "UPDATE api_keys SET audience='everunfold', "
            "expires_at=now()-interval '1 second' WHERE id=$1",
            machine_id,
        )
    )
    assert post("/v1/reader/access", target, machine["secret"]).status_code == 401
    asyncio.run(
        sql(
            "UPDATE api_keys SET expires_at=now()+interval '1 hour', "
            "permissions=$2::jsonb WHERE id=$1",
            machine_id,
            '{"content:read":"all"}',
        )
    )
    assert post("/v1/reader/access", target, machine["secret"]).status_code == 403

    # Hold only the owner row while admission passes its initial live lookup. Revoke
    # the key before releasing that row; the final fence must re-read authorization.
    async def race() -> None:
        dsn = (
            make_url(settings.database.url.get_secret_value())
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        connection = await asyncpg.connect(dsn)
        mutator = await asyncpg.connect(dsn)
        transaction = connection.transaction()
        await transaction.start()
        task = None
        try:
            pid = await connection.fetchval("SELECT pg_backend_pid()")
            await connection.fetch(
                "SELECT id FROM users WHERE id="
                "(SELECT user_id FROM api_keys WHERE id=$1) FOR UPDATE",
                UUID(other["key"]["id"]),
            )
            async with httpx.AsyncClient(trust_env=False, timeout=12) as reader:
                task = asyncio.create_task(
                    reader.post(
                        api_b + "/v1/reader/access",
                        json=target,
                        headers={"Authorization": "Bearer " + other["secret"]},
                    )
                )
                for _ in range(100):
                    blocked = await mutator.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE $1=ANY(pg_blocking_pids(pid)))",
                        pid,
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.03)
                else:
                    raise AssertionError("Reader did not reach the authorization fence")
                await mutator.execute(
                    "UPDATE api_keys SET revoked_at=now() WHERE id=$1", UUID(other["key"]["id"])
                )
                await transaction.commit()
                assert (await task).status_code == 401
        finally:
            if task and not task.done():
                task.cancel()
            if connection.is_in_transaction():
                await transaction.rollback()
            await connection.close()
            await mutator.close()

    asyncio.run(race())
    revoked = client.post(
        "/v1/account/api-keys/revoke",
        json={"operation_id": str(uuid4()), "key_id": machine["key"]["id"]},
        headers=csrf,
    )
    assert revoked.status_code == 200, revoked.text
    assert post("/v1/reader/access", target, machine["secret"]).status_code == 401
    assert post("/v1/reader/comments", submitted, machine["secret"]).status_code == 401
    audited = asyncio.run(
        sql(
            "SELECT details FROM auth_audit_events WHERE action='api_key_captcha_bypass' "
            "AND user_id=(SELECT user_id FROM api_keys WHERE id=$1)",
            machine_id,
        )
    )
    assert audited
