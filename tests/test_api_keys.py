"""Real API/Redis/PG authorization, signed queries and one-time credential boundaries."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx2 as httpx
import pytest
from sqlalchemy import select

from app.core.database import Database
from app.modules.auth.application_signatures import signed_headers, verify_signature
from app.modules.auth.credentials_models import ApiKey
from tests.test_accounts import PASSWORD, AccountsTest, headers
from tests.test_accounts import accounts as accounts_fixture

accounts = accounts_fixture


def create_application(
    ctx: AccountsTest, *, audience: str = "everunfold", callback: str = ""
) -> dict[str, Any]:
    response = ctx.root.post(
        "/v1/admin/api-key-applications",
        json={
            "operation_id": str(uuid4()),
            "app_id": "test-" + uuid4().hex,
            "audience": audience,
            "current_password": PASSWORD,
            "value": {
                "name": "测试项目",
                "callback_url": callback,
                "scopes": [{"code": "content:read", "label": "读取内容", "owner_scope": True}],
            },
        },
        headers=headers(ctx.root),
    )
    assert response.status_code == 201, response.text
    return dict(response.json()["data"])


def create_key(
    ctx: AccountsTest, app: str = "everunfold", **extra: object
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "operation_id": str(uuid4()),
        "app_id": app,
        "name": "自动备份",
        "permissions": {"content:read": "all"},
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "current_password": PASSWORD,
        **extra,
    }
    result = ctx.root.post("/v1/account/api-keys", json=body, headers=headers(ctx.root))
    assert result.status_code == 201, result.text
    return result.json()["data"], body


@pytest.fixture(autouse=True)
def master(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVERUNFOLD_API_APPLICATION_MASTER_KEY", "test_" + "z" * 43)


def test_key_one_time_delivery_and_explicit_admin_ceiling(accounts: AccountsTest) -> None:
    ctx = accounts
    result, body = create_key(ctx)
    token = result["secret"]
    assert token.startswith("efk_")
    replay = ctx.root.post("/v1/account/api-keys", json=body, headers=headers(ctx.root))
    assert replay.status_code == 201 and replay.json()["data"]["secret"] is None
    listing = ctx.root.get("/v1/account/api-keys")
    assert token not in listing.text and "token_hash" not in listing.text
    program = ctx.client()
    program.headers["Authorization"] = "bearer " + token
    assert program.get("/v1/admin/content/types").status_code == 200
    # An administrator's read key cannot use administrator cookies to exceed its ceiling.
    browser_headers = headers(ctx.root)
    ctx.root.headers["Authorization"] = "Bearer " + token
    assert ctx.root.patch("/v1/admin/settings", json={}, headers=browser_headers).status_code == 403
    assert ctx.root.get("/v1/account/profile").status_code == 403
    ctx.root.headers["Authorization"] = "Bearer efk_invalid"
    assert ctx.root.get("/v1/admin/content/types").status_code == 401
    del ctx.root.headers["Authorization"]
    response = ctx.root.post(
        "/v1/account/api-keys/revoke",
        json={"operation_id": str(uuid4()), "key_id": result["key"]["id"]},
        headers=headers(ctx.root),
    )
    assert response.status_code == 200
    assert program.get("/v1/admin/content/types").status_code == 401


def query(
    ctx: AccountsTest,
    app: dict[str, Any],
    token: str,
    mode: str = "read",
    *,
    timestamp: int | None = None,
) -> tuple[httpx.Response, bytes, dict[str, str]]:
    target = app["application"]["query_url"]
    body = json.dumps({"token": token, "mode": mode}, separators=(",", ":")).encode()
    h = signed_headers(
        app["query_secret"],
        app["application"]["id"],
        app["application"]["query_key_id"],
        "POST",
        target,
        body,
        timestamp=timestamp,
    )
    return ctx.root.post("/v1/auth/api-keys/introspect", content=body, headers=h), body, h


def test_signed_query_binding_replay_absolute_deadline_and_revocation(
    accounts: AccountsTest,
) -> None:
    ctx = accounts
    app = create_application(ctx)
    key, _ = create_key(ctx, app["application"]["id"])
    response, body, h = query(ctx, app, key["secret"])
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["active"] and result["audience"] == "everunfold"
    assert result["permissions"] == {"content:read": "all"}
    assert datetime.fromisoformat(result["cache_until"]) <= (
        datetime.fromisoformat(result["checked_at"]) + timedelta(seconds=30)
    )
    assert (
        verify_signature(
            app["query_secret"],
            app["application"]["id"],
            app["application"]["query_key_id"],
            "RESPONSE",
            app["application"]["query_url"],
            response.content,
            response.headers,
        )
        == h["X-Everunfold-Nonce"]
    )
    assert ctx.root.post("/v1/auth/api-keys/introspect", content=body, headers=h).status_code == 401
    assert (
        ctx.root.post("/v1/auth/api-keys/introspect", content=body + b" ", headers=h).status_code
        == 401
    )
    assert query(ctx, app, key["secret"], timestamp=int(time.time()) - 121)[0].status_code == 401
    write = query(ctx, app, key["secret"], "write")[0].json()["data"]
    assert write["cache_until"] == write["checked_at"]
    another = create_application(ctx)
    assert query(ctx, another, key["secret"])[0].json()["data"] == {"active": False}
    revoked = ctx.root.post(
        "/v1/account/api-keys/revoke",
        json={"operation_id": str(uuid4()), "key_id": key["key"]["id"]},
        headers=headers(ctx.root),
    )
    assert revoked.status_code == 200
    assert query(ctx, app, key["secret"])[0].json()["data"] == {"active": False}


def test_external_audience_cannot_access_main_project(accounts: AccountsTest) -> None:
    ctx = accounts
    app = create_application(ctx, audience="self")
    key, _ = create_key(ctx, app["application"]["id"])
    assert query(ctx, app, key["secret"])[0].json()["data"]["audience"] == app["application"]["id"]
    assert (
        ctx.root.get(
            "/v1/admin/content/types", headers={"Authorization": "Bearer " + key["secret"]}
        ).status_code
        == 401
    )


def test_secrets_are_absent_from_operations_and_storage(accounts: AccountsTest) -> None:
    app = create_application(accounts)
    key, _ = create_key(accounts, app["application"]["id"])

    async def inspect() -> None:
        from app.modules.auth.models import AccountOperation

        db = Database(accounts.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                stored = await session.scalar(select(ApiKey).where(ApiKey.id == key["key"]["id"]))
                assert stored and stored.token_hash != key["secret"]
                operations = list(
                    await session.scalars(
                        select(AccountOperation.response).where(
                            AccountOperation.actor_id == stored.user_id
                        )
                    )
                )
                encoded = json.dumps(operations)
                for secret in (key["secret"], app["query_secret"], app["callback_secret"]):
                    assert secret not in encoded
        finally:
            await db.close()

    asyncio.run(inspect())
