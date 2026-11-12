"""Browser authentication boundaries and real PostgreSQL account/session behavior."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AuthConfig, CORSConfig, Settings, load_settings
from app.core.database import Database
from app.entrypoints.api import create_app
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.models import AuthAuditEvent, AuthRateLimit, BrowserSession, User
from app.modules.auth.schemas import AuthContract
from app.modules.auth.security import (
    csrf_token,
    new_session_token,
    token_digest,
    valid_csrf,
    verify_password,
)
from tools.export_auth_contract import build_schema

ORIGIN = "http://127.0.0.1:3000"


def test_csrf_is_bound_to_session_and_rejects_non_ascii() -> None:
    token, other = new_session_token(), new_session_token()
    assert valid_csrf(token, csrf_token(token))
    for invalid in (None, "", "é" * 64, csrf_token(other), "a" * 10000):
        assert not valid_csrf(token, invalid)
    assert csrf_token(token) != token_digest(token)


def test_auth_contract_matches_published_schema() -> None:
    document = json.loads(Path("contracts/auth-v0.1.schema.json").read_text(encoding="utf-8"))
    assert document == build_schema()
    assert AuthContract.model_json_schema()["title"] == "AuthContract"
    assert document["x-everunfold-credentials"] == "include"


@pytest.mark.parametrize(
    "auth",
    [
        {"cookie_name": "__Host-session"},
        {"cookie_name": "__Secure-session"},
        {"cookie_samesite": "none", "cookie_secure": False},
        {"cookie_domain": "evil.example"},
    ],
)
def test_cookie_config_rejects_incompatible_scope(
    test_settings: Settings, auth: dict[str, Any]
) -> None:
    values = test_settings.model_dump()
    values["auth"] = auth
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


def test_https_requires_secure_and_cors_rejects_wildcards(test_settings: Settings) -> None:
    values = test_settings.model_dump()
    values["BACKEND_BASE_URL"] = "https://api.example.com"
    values["auth"] = {}
    assert Settings.model_validate(values).auth.cookie_secure is True
    values["auth"] = {"cookie_secure": False}
    with pytest.raises(ValidationError):
        Settings.model_validate(values)
    values["BACKEND_BASE_URL"] = "http://api.example.com"
    values["auth"] = {"cookie_secure": True}
    with pytest.raises(ValidationError):
        Settings.model_validate(values)
    with pytest.raises(ValidationError):
        CORSConfig(allowed_origins=["*"])


def test_origin_cors_body_limit_and_auth_errors_are_private(test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"cors": CORSConfig(allowed_origins=[ORIGIN])})
    with TestClient(create_app(settings)) as client:
        preflight = client.options(
            "/v1/auth/login",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == ORIGIN
        assert preflight.headers["access-control-allow-credentials"] == "true"
        for origin in (None, "null", "https://evil.example", ORIGIN + ".evil.example"):
            headers = {} if origin is None else {"Origin": origin}
            response = client.post(
                "/v1/auth/login", json={"username": "admin", "password": "secret"}, headers=headers
            )
            assert response.status_code == 403
            assert response.json()["code"] == 40310
            assert "no-store" in response.headers["cache-control"]
            assert "set-cookie" not in response.headers
        too_large = client.post(
            "/v1/auth/login",
            content=b" " * 17000,
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        )
        assert too_large.status_code == 413
        assert too_large.headers["access-control-allow-origin"] == ORIGIN
        assert too_large.headers["access-control-allow-credentials"] == "true"
        invalid = client.post(
            "/v1/auth/login",
            json={"username": "a", "password": "never-echo", "unknown": True},
            headers={"Origin": ORIGIN},
        )
        assert invalid.status_code == 422 and "never-echo" not in invalid.text
        assert "no-store" in invalid.headers["cache-control"]
        assert invalid.headers["access-control-allow-origin"] == ORIGIN
        assert invalid.headers["access-control-allow-credentials"] == "true"


@pytest.fixture
def auth_client() -> Iterator[tuple[TestClient, Settings, str, str]]:
    if os.environ.get("RUN_DATABASE_TESTS") != "1":
        pytest.skip("Dedicated PostgreSQL test database required")
    settings = load_settings("config.test.local.toml")
    assert make_url(settings.database.url.get_secret_value()).database == "everunfold_test"
    assert settings.redis.namespace_scope.startswith("test")
    settings = settings.model_copy(
        update={
            "cors": CORSConfig(allowed_origins=[ORIGIN]),
            "auth": AuthConfig(login_attempts_per_account=3),
        }
    )
    suffix = uuid4().hex
    name, peer = "auth_" + suffix, "qa_" + suffix
    asyncio.run(seed_admin(settings, name, "123456"))
    try:
        with TestClient(create_app(settings), client=(peer, 50000)) as client:
            yield client, settings, name, peer
    finally:

        async def clean() -> None:
            database = Database(settings.database.url.get_secret_value())
            try:
                async with database.session_factory() as session:
                    user_id = await session.scalar(select(User.id).where(User.username == name))
                    await session.execute(
                        delete(AuthAuditEvent).where(AuthAuditEvent.user_id == user_id)
                    )
                    await session.execute(delete(User).where(User.id == user_id))
                    keys = [
                        "bootstrap:" + peer,
                        "login-ip:" + peer,
                        "login-account:" + name,
                        "login-account:" + name + "@example.test",
                    ]
                    await session.execute(
                        delete(AuthRateLimit).where(
                            AuthRateLimit.key_hash.in_(
                                [hashlib.sha256(key.encode()).hexdigest() for key in keys]
                            )
                        )
                    )
                    await session.commit()
            finally:
                await database.close()

        asyncio.run(clean())


def login(client: TestClient, name: str) -> dict[str, Any]:
    initial = client.get("/v1/auth/session", headers={"Origin": ORIGIN})
    assert initial.status_code == 200
    response = client.post(
        "/v1/auth/login",
        json={"username": name, "password": "123456"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": initial.json()["data"]["csrf_token"]},
    )
    assert response.status_code == 200
    return dict(response.json()["data"])


def test_verified_email_login_and_shared_account_limit(
    auth_client: tuple[TestClient, Settings, str, str],
) -> None:
    client, settings, name, _ = auth_client
    email = name + "@example.test"

    async def set_email(verified: bool) -> None:
        database = Database(settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                await session.execute(
                    update(User)
                    .where(User.username == name)
                    .values(email=email, email_verified_at=datetime.now(UTC) if verified else None)
                )
                await session.commit()
        finally:
            await database.close()

    asyncio.run(set_email(False))
    csrf = client.get("/v1/auth/session").json()["data"]["csrf_token"]
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    denied = client.post(
        "/v1/auth/login", json={"username": email, "password": "123456"}, headers=headers
    )
    assert denied.status_code == 401
    asyncio.run(set_email(True))
    for identifier in (name, email.upper(), name):
        response = client.post(
            "/v1/auth/login",
            json={"username": identifier, "password": "incorrect"},
            headers=headers,
        )
        assert response.status_code == 401
    blocked = client.post(
        "/v1/auth/login", json={"username": email, "password": "123456"}, headers=headers
    )
    assert blocked.status_code == 429


def test_verified_email_login_accepts_case_insensitive_address(
    auth_client: tuple[TestClient, Settings, str, str],
) -> None:
    client, settings, name, _ = auth_client
    email = name + "@example.test"

    async def set_email() -> None:
        database = Database(settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                await session.execute(
                    update(User)
                    .where(User.username == name)
                    .values(email=email, email_verified_at=datetime.now(UTC))
                )
                await session.commit()
        finally:
            await database.close()

    asyncio.run(set_email())
    assert login(client, email.upper())["user"]["username"] == name


def test_login_rotates_cookie_csrf_and_logout_revokes(
    auth_client: tuple[TestClient, Settings, str, str],
) -> None:
    client, settings, name, _ = auth_client
    guest = client.get("/v1/auth/session", headers={"Origin": ORIGIN})
    old_cookie = client.cookies.get(settings.auth.cookie_name)
    old_csrf = guest.json()["data"]["csrf_token"]
    assert guest.json()["data"]["authenticated"] is False
    assert "HttpOnly" in guest.headers["set-cookie"] and "Path=/v1" in guest.headers["set-cookie"]
    assert "SameSite=lax" in guest.headers["set-cookie"]
    state = login(client, name)
    active_cookie = client.cookies.get(settings.auth.cookie_name)
    assert state["authenticated"] and state["user"]["role"] == "admin"
    assert old_cookie != active_cookie and old_csrf != state["csrf_token"]
    assert client.get("/v1/admin/session", headers={"Origin": ORIGIN}).status_code == 200
    old = client.get(
        "/v1/admin/session",
        headers={"Origin": ORIGIN, "Cookie": f"{settings.auth.cookie_name}={old_cookie}"},
    )
    assert old.status_code == 401
    missing = client.post("/v1/auth/logout", headers={"Origin": ORIGIN})
    assert missing.status_code == 403
    stale = client.post("/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": old_csrf})
    assert stale.status_code == 403
    logout = client.post(
        "/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": state["csrf_token"]}
    )
    assert logout.status_code == 200 and logout.json()["data"]["user"] is None
    assert (
        client.get(
            "/v1/admin/session",
            headers={"Origin": ORIGIN, "Cookie": f"{settings.auth.cookie_name}={active_cookie}"},
        ).status_code
        == 401
    )

    async def stored() -> None:
        database = Database(settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                user = await session.scalar(select(User).where(User.username == name))
                assert user is not None and user.password_hash.startswith("$argon2id$")
                assert verify_password(user.password_hash, "123456")
                assert await session.get(BrowserSession, str(active_cookie)) is None
                assert await session.get(BrowserSession, token_digest(str(active_cookie))) is None
        finally:
            await database.close()

    asyncio.run(stored())


def test_wrong_credentials_rate_limit_and_csrf_before_password(
    auth_client: tuple[TestClient, Settings, str, str],
) -> None:
    client, _, name, _ = auth_client
    state = client.get("/v1/auth/session").json()["data"]
    for _ in range(2):
        denied = client.post(
            "/v1/auth/login",
            json={"username": name, "password": "wrong"},
            headers={"Origin": ORIGIN},
        )
        assert denied.status_code == 403
    for _ in range(3):
        denied = client.post(
            "/v1/auth/login",
            json={"username": name, "password": "wrong"},
            headers={"Origin": ORIGIN, "X-CSRF-Token": state["csrf_token"]},
        )
        assert denied.status_code == 401 and denied.json()["code"] == 40110
    limited = client.post(
        "/v1/auth/login",
        json={"username": name, "password": "123456"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": state["csrf_token"]},
    )
    assert limited.status_code == 429 and limited.json()["code"] == 42901
    assert int(limited.headers["retry-after"]) > 0


def test_admin_permission_disabled_user_and_expiry(
    auth_client: tuple[TestClient, Settings, str, str],
) -> None:
    client, settings, name, _ = auth_client
    login(client, name)

    async def change(*, role: str = "admin", active: bool = True, expire: bool = False) -> None:
        database = Database(settings.database.url.get_secret_value())
        try:
            async with database.session_factory() as session:
                await session.execute(
                    update(User).where(User.username == name).values(role=role, active=active)
                )
                if expire:
                    await session.execute(
                        update(BrowserSession)
                        .where(
                            BrowserSession.token_hash
                            == token_digest(str(client.cookies.get(settings.auth.cookie_name)))
                        )
                        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
                    )
                await session.commit()
        finally:
            await database.close()

    asyncio.run(change(role="user"))
    assert client.get("/v1/admin/session").status_code == 403
    asyncio.run(change(active=False))
    assert client.get("/v1/admin/session").status_code == 401
    asyncio.run(change())
    login(client, name)
    asyncio.run(change(expire=True))
    assert client.get("/v1/admin/session").status_code == 401


def test_failed_database_commit_never_sets_session_cookie(
    auth_client: tuple[TestClient, Settings, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _, _ = auth_client

    async def fail_commit(self: AsyncSession) -> None:
        raise SQLAlchemyError("commit unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", fail_commit)
        response = client.get("/v1/auth/session", headers={"Origin": ORIGIN})
    assert response.status_code == 503 and response.json()["success"] is False
    assert "set-cookie" not in response.headers
    assert "no-store" in response.headers["cache-control"]


def test_sessions_and_login_limits_are_shared_by_independent_processes(
    auth_client: tuple[TestClient, Settings, str, str],
) -> None:
    _, _, name, peer = auth_client
    script = """
import asyncio, json, sys
from anyio import CapacityLimiter
from sqlalchemy.engine import make_url
from app.core.config import AuthConfig, load_settings
from app.core.database import Database
from app.modules.auth.service import AuthService, AuthFailure
payload = json.load(sys.stdin)
async def run():
    settings = load_settings('config.test.local.toml')
    assert make_url(settings.database.url.get_secret_value()).database == 'everunfold_test'
    database = Database(settings.database.url.get_secret_value())
    try:
        async with database.session_factory() as session:
            service = AuthService(session, AuthConfig(login_attempts_per_account=3),
                                  CapacityLimiter(2))
            try:
                if payload['mode'] in ('login', 'wrong'):
                    guest = await service.read(None, payload['peer'])
                    result = await service.login(guest.cookie, guest.state.csrf_token,
                        payload['name'],
                        '123456' if payload['mode'] == 'login' else 'wrong', payload['peer'])
                elif payload['mode'] == 'logout':
                    result = await service.logout(payload['cookie'], payload['csrf'])
                else:
                    result = await service.read(payload['cookie'], payload['peer'], admin=True)
                print(json.dumps({'status': 200, 'cookie': result.cookie,
                                  'state': result.state.model_dump(mode='json')}))
            except AuthFailure as exc:
                print(json.dumps({'status': exc.status, 'code': exc.code}))
    finally:
        await database.close()
asyncio.run(run())
"""

    def start(mode: str, **values: Any) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps({"mode": mode, "name": name, "peer": peer, **values}))
        process.stdin.close()
        process.stdin = None
        return process

    def result(process: subprocess.Popen[str]) -> dict[str, Any]:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr[-1000:]
        return dict(json.loads(stdout))

    created = result(start("login"))
    assert created["status"] == 200
    token = created["cookie"]
    state = result(start("read", cookie=token))
    assert state["status"] == 200 and state["state"]["user"]["username"] == name
    assert (
        result(start("logout", cookie=token, csrf=created["state"]["csrf_token"]))["status"] == 200
    )
    assert result(start("read", cookie=token))["status"] == 401
    # The successful login consumed one of three account attempts. Three racing failures
    # across new processes may perform only two further password checks in this window.
    processes = [start("wrong") for _ in range(3)]
    assert sorted(result(process)["status"] for process in processes) == [401, 401, 429]
