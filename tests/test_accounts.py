"""Real private account boundaries and credential changes, isolated by test-user prefix."""

import asyncio
import json
import os
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select, update
from sqlalchemy.engine import make_url

from app.core.config import CORSConfig, Settings, load_settings
from app.core.database import Database
from app.entrypoints.api import create_app
from app.modules.auth.accounts_schemas import ProfileFields, UserSave
from app.modules.auth.accounts_service import AccountsService, account_mutation_lock
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.credentials_models import ApiApplication, ApiKey, AuthCallbackEvent
from app.modules.auth.models import AccountOperation, AuthAuditEvent, BrowserSession, User
from app.modules.auth.permissions import Actor, intersect_permissions, user_permissions
from app.modules.auth.service import AuthFailure, AuthService
from tools.export_accounts_contract import build_schema

ORIGIN = "http://127.0.0.1:3000"
PASSWORD = "Account-root-12345"
TEMPORARY = "Account-temp-12345"


def test_account_contract_matches_published_source() -> None:
    assert json.loads(Path("contracts/accounts-v1.schema.json").read_text()) == build_schema()


@dataclass
class AccountsTest:
    settings: Settings
    name: str
    root: TestClient
    stack: ExitStack

    def client(self) -> TestClient:
        return self.stack.enter_context(TestClient(create_app(self.settings)))


def signed_in(client: TestClient, name: str, password: str = PASSWORD) -> dict[str, Any]:
    initial = client.get("/v1/auth/session", headers={"Origin": ORIGIN})
    assert initial.status_code == 200
    result = client.post(
        "/v1/auth/login",
        json={"username": name, "password": password},
        headers={"Origin": ORIGIN, "X-CSRF-Token": initial.json()["data"]["csrf_token"]},
    )
    assert result.status_code == 200, result.text
    return dict(result.json()["data"])


def headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": client.get("/v1/auth/session").json()["data"]["csrf_token"],
    }


def create_user(ctx: AccountsTest, *, role: str = "user", suffix: str = "member") -> dict[str, Any]:
    response = ctx.root.post(
        "/v1/admin/users",
        json={
            "operation_id": str(uuid4()),
            "username": ctx.name + "_" + suffix,
            "display_name": "成员",
            "password": TEMPORARY,
            "current_password": PASSWORD,
            "role": role,
            "grants": {},
        },
        headers=headers(ctx.root),
    )
    assert response.status_code == 201, response.text
    return dict(response.json()["data"])


@pytest.fixture
def accounts() -> Iterator[AccountsTest]:
    if os.environ.get("RUN_DATABASE_TESTS") != "1":
        pytest.skip("Dedicated PostgreSQL test database required")
    settings = load_settings("config.test.local.toml")
    assert make_url(settings.database.url.get_secret_value()).database == "everunfold_test"
    assert settings.redis.namespace_scope.startswith("test")
    settings = settings.model_copy(update={"cors": CORSConfig(allowed_origins=[ORIGIN])})
    name = "acct_" + uuid4().hex
    asyncio.run(seed_admin(settings, name, PASSWORD))
    try:
        with ExitStack() as stack:
            client = stack.enter_context(TestClient(create_app(settings)))
            signed_in(client, name)
            yield AccountsTest(settings, name, client, stack)
    finally:

        async def clean() -> None:
            database = Database(settings.database.url.get_secret_value())
            try:
                async with database.session_factory() as session:
                    ids = list(
                        await session.scalars(select(User.id).where(User.username.startswith(name)))
                    )
                    apps = select(ApiApplication.id).where(ApiApplication.created_by.in_(ids))
                    await session.execute(
                        delete(AuthCallbackEvent).where(AuthCallbackEvent.app_id.in_(apps))
                    )
                    await session.execute(delete(ApiKey).where(ApiKey.user_id.in_(ids)))
                    await session.execute(
                        delete(ApiApplication).where(ApiApplication.created_by.in_(ids))
                    )
                    await session.execute(
                        delete(AccountOperation).where(AccountOperation.actor_id.in_(ids))
                    )
                    await session.execute(
                        delete(AuthAuditEvent).where(
                            or_(
                                AuthAuditEvent.user_id.in_(ids),
                                AuthAuditEvent.target_user_id.in_(ids),
                            )
                        )
                    )
                    await session.execute(
                        delete(BrowserSession).where(BrowserSession.user_id.in_(ids))
                    )
                    await session.execute(delete(User).where(User.id.in_(ids)))
                    await session.commit()
            finally:
                await database.close()

        asyncio.run(clean())


def test_permission_intersection_never_inherits_admin_privileges() -> None:
    user = User(
        id=uuid4(),
        username="author",
        display_name="作者",
        role="user",
        active=True,
        must_change_password=False,
        grants={"everunfold": {"content:read": "all", "content:write": "own"}},
    )
    owner, other = user.id, uuid4()
    permissions = intersect_permissions(
        user_permissions(user),
        {"content:read": "own", "content:write": "all", "settings:write": "all"},
    )
    actor = Actor(owner, "author", "作者", "user", permissions, "api_key", uuid4())
    assert actor.allows("content:write", owner)
    assert not actor.allows("content:write", other)
    assert not actor.has("settings:write")
    user.active = False
    assert user_permissions(user) == {}


def test_profile_is_private_versioned_idempotent_and_cannot_set_privileges(
    accounts: AccountsTest,
) -> None:
    member = create_user(accounts)
    client = accounts.client()
    state = signed_in(client, member["username"], TEMPORARY)
    assert state["user"]["must_change_password"] is True
    assert state["user"]["permissions"] == {}
    assert client.get("/v1/admin/users").status_code == 403
    current = client.get("/v1/account/profile")
    assert "no-store" in current.headers["cache-control"]
    data = current.json()["data"]
    value = {key: data[key] for key in ProfileFields.model_fields}
    value.update(
        display_name="新名字",
        bio="English / 中文",
        preferred_locale="en",
        email="member@example.test",
    )
    payload = {"operation_id": str(uuid4()), "expected_revision": data["revision"], "value": value}
    response = client.patch("/v1/account/profile", json=payload, headers=headers(client))
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["display_name"] == "新名字" and result["email_verified"] is False
    assert "password_hash" not in response.text and TEMPORARY not in response.text
    assert PASSWORD not in response.text and result["grants"] == {}
    assert (
        client.patch("/v1/account/profile", json=payload, headers=headers(client)).json()["data"]
        == result
    )
    changed = {**payload, "value": {**value, "display_name": "其他"}}
    assert (
        client.patch("/v1/account/profile", json=changed, headers=headers(client)).status_code
        == 409
    )
    stale = {**payload, "operation_id": str(uuid4())}
    assert (
        client.patch("/v1/account/profile", json=stale, headers=headers(client)).status_code == 409
    )
    elevated = {
        **stale,
        "expected_revision": result["revision"],
        "value": {**value, "role": "admin"},
    }
    assert (
        client.patch("/v1/account/profile", json=elevated, headers=headers(client)).status_code
        == 422
    )
    assert (
        client.get("/v1/account/profile", headers={"Authorization": "Bearer invalid"}).status_code
        == 403
    )
    assert accounts.client().get("/v1/account/profile").status_code == 401


def test_new_account_retries_never_expose_password_and_search_is_literal(
    accounts: AccountsTest,
) -> None:
    payload = {
        "operation_id": str(uuid4()),
        "username": accounts.name + "_retry",
        "display_name": "Retry%_",
        "password": TEMPORARY,
        "current_password": PASSWORD,
    }
    first = accounts.root.post("/v1/admin/users", json=payload, headers=headers(accounts.root))
    assert first.status_code == 201
    second = accounts.root.post("/v1/admin/users", json=payload, headers=headers(accounts.root))
    assert second.status_code == 201 and second.json()["data"]["id"] == first.json()["data"]["id"]
    assert TEMPORARY not in second.text and PASSWORD not in second.text
    assert (
        accounts.root.post(
            "/v1/admin/users",
            json={**payload, "password": "Changed-temp-12345"},
            headers=headers(accounts.root),
        ).status_code
        == 409
    )
    result = accounts.root.get("/v1/admin/users", params={"q": "Retry%_"}).json()["data"]
    assert result["total"] == 1


def test_password_change_rotates_current_session_and_revokes_other_sessions(
    accounts: AccountsTest,
) -> None:
    member = create_user(accounts)
    a, b = accounts.client(), accounts.client()
    signed_in(a, member["username"], TEMPORARY)
    signed_in(b, member["username"], TEMPORARY)
    old_token = a.cookies.get(accounts.settings.auth.cookie_name)
    current = a.get("/v1/account/profile").json()["data"]
    response = a.post(
        "/v1/account/password",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": current["revision"],
            "current_password": TEMPORARY,
            "new_password": "Account-fresh-12345",
            "revoke_api_keys": True,
        },
        headers=headers(a),
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["user"]["must_change_password"] is False
    assert a.cookies.get(accounts.settings.auth.cookie_name) != old_token
    assert b.get("/v1/account/profile").status_code == 401
    assert (
        a.get(
            "/v1/account/profile",
            headers={"Cookie": accounts.settings.auth.cookie_name + "=" + str(old_token)},
        ).status_code
        == 401
    )
    assert a.get("/v1/account/profile").status_code == 200


def test_admin_disable_revokes_existing_sessions_and_conflicting_updates(
    accounts: AccountsTest,
) -> None:
    member = create_user(accounts)
    client = accounts.client()
    signed_in(client, member["username"], TEMPORARY)
    value = {key: member[key] for key in ProfileFields.model_fields}
    payload = {
        "operation_id": str(uuid4()),
        "expected_revision": member["revision"],
        "value": value,
        "role": "user",
        "active": False,
        "grants": {},
        "current_password": PASSWORD,
    }
    response = accounts.root.patch(
        "/v1/admin/users/" + member["id"], json=payload, headers=headers(accounts.root)
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["auth_version"] == member["auth_version"] + 1
    assert client.get("/v1/account/profile").status_code == 401
    assert (
        accounts.root.patch(
            "/v1/admin/users/" + member["id"],
            json={**payload, "operation_id": str(uuid4())},
            headers=headers(accounts.root),
        ).status_code
        == 409
    )


def test_last_admin_guard_inside_a_real_transaction(accounts: AccountsTest) -> None:
    root_id = accounts.root.get("/v1/account/profile").json()["data"]["id"]
    raw = str(accounts.root.cookies.get(accounts.settings.auth.cookie_name))

    async def exercise() -> None:
        from uuid import UUID

        from anyio import CapacityLimiter

        database = Database(accounts.settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                await account_mutation_lock(session)
                await session.execute(
                    update(User)
                    .where(User.role == "admin", User.id != UUID(root_id))
                    .values(active=False)
                )
                auth = AuthService(session, accounts.settings.auth, CapacityLimiter(2))
                actor = await auth.require_user(raw, None, write=False)
                value = ProfileFields(display_name=actor.display_name)
                with pytest.raises(AuthFailure) as error:
                    await AccountsService(session, auth).save_user(
                        actor,
                        actor.id,
                        UserSave(
                            operation_id=uuid4(),
                            expected_revision=actor.revision,
                            value=value,
                            role="user",
                            active=True,
                            grants={},
                            current_password=PASSWORD,
                        ),
                    )
                assert error.value.code == 40963
                await session.rollback()
        finally:
            await database.close()

    asyncio.run(exercise())
    assert accounts.root.get("/v1/admin/session").status_code == 200
