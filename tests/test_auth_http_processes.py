"""Real Uvicorn processes prove cross-origin cookies and shared persistent sessions."""

import asyncio
import hashlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx2 as httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.models import AuthAuditEvent, AuthRateLimit, BrowserSession, User
from app.modules.auth.security import token_digest

ORIGIN = "http://127.0.0.1:3000"
ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def uvicorn_process(port: int, environment: dict[str, str]) -> Iterator[None]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.entrypoints.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-proxy-headers",
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/health/live"
    deadline = time.monotonic() + 20
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr is not None else ""
                pytest.fail(f"Uvicorn exited before readiness: {stderr[-2000:]}")
            try:
                if httpx.get(url, timeout=0.5).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail(f"Uvicorn did not become live on {port}")
        yield
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if process.stderr is not None:
            process.stderr.close()


def private_cors(response: httpx.Response) -> None:
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1", reason="Explicit isolated database test required"
)
def test_two_uvicorn_processes_share_login_and_revoke_sessions() -> None:
    settings = load_settings(ROOT / "config.test.local.toml")
    database_url = settings.database.url.get_secret_value()
    assert make_url(database_url).database == "everunfold_test"
    assert settings.redis.namespace_scope.startswith("test")
    assert ORIGIN in settings.cors.allowed_origins

    suffix = uuid4().hex
    name = "http_auth_" + suffix
    cookie_name = settings.auth.cookie_name
    rate_keys = [
        "bootstrap:127.0.0.1",
        "login-ip:127.0.0.1",
        "login-account:" + name,
    ]
    tokens: set[str] = set()

    async def cleanup() -> None:
        database = Database(database_url)
        try:
            async with database.session_factory() as session:
                user_id = await session.scalar(select(User.id).where(User.username == name))
                if user_id is not None:
                    await session.execute(
                        delete(AuthAuditEvent).where(AuthAuditEvent.user_id == user_id)
                    )
                    await session.execute(delete(User).where(User.id == user_id))
                if tokens:
                    await session.execute(
                        delete(BrowserSession).where(
                            BrowserSession.token_hash.in_([token_digest(token) for token in tokens])
                        )
                    )
                await session.execute(
                    delete(AuthRateLimit).where(
                        AuthRateLimit.key_hash.in_(
                            [hashlib.sha256(key.encode()).hexdigest() for key in rate_keys]
                        )
                    )
                )
                await session.commit()
        finally:
            await database.close()

    asyncio.run(cleanup())
    asyncio.run(seed_admin(settings, name, "123456"))
    environment = {
        **os.environ,
        "CONFIG_FILE": str(ROOT / "config.test.local.toml"),
        "PYTHONUNBUFFERED": "1",
    }
    first = free_port()
    second = free_port()
    while second == first:
        second = free_port()
    try:
        with uvicorn_process(first, environment), uvicorn_process(second, environment):
            with httpx.Client(timeout=5) as client:
                preflight = client.options(
                    f"http://127.0.0.1:{first}/v1/auth/login",
                    headers={
                        "Origin": ORIGIN,
                        "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
                    },
                )
                assert preflight.status_code == 200
                assert preflight.headers["access-control-allow-origin"] == ORIGIN
                assert preflight.headers["access-control-allow-credentials"] == "true"

                initial = client.get(
                    f"http://127.0.0.1:{first}/v1/auth/session",
                    headers={"Origin": ORIGIN},
                )
                assert initial.status_code == 200
                private_cors(initial)
                initial_cookie = client.cookies.get(cookie_name)
                assert initial_cookie is not None
                tokens.add(initial_cookie)
                csrf = initial.json()["data"]["csrf_token"]

                login = client.post(
                    f"http://127.0.0.1:{first}/v1/auth/login",
                    json={"username": name, "password": "123456"},
                    headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                )
                assert login.status_code == 200
                private_cors(login)
                assert login.json()["data"]["user"]["role"] == "admin"
                active_cookie = client.cookies.get(cookie_name)
                assert active_cookie is not None and active_cookie != initial_cookie
                tokens.add(active_cookie)
                active_csrf = login.json()["data"]["csrf_token"]

                admin = client.get(
                    f"http://127.0.0.1:{second}/v1/admin/session",
                    headers={"Origin": ORIGIN},
                )
                assert admin.status_code == 200
                private_cors(admin)
                assert admin.json()["data"]["user"]["username"] == name

                logout = client.post(
                    f"http://127.0.0.1:{second}/v1/auth/logout",
                    headers={"Origin": ORIGIN, "X-CSRF-Token": active_csrf},
                )
                assert logout.status_code == 200
                private_cors(logout)
                guest_cookie = client.cookies.get(cookie_name)
                assert guest_cookie is not None and guest_cookie not in {
                    initial_cookie,
                    active_cookie,
                }
                tokens.add(guest_cookie)

            with httpx.Client(timeout=5) as client:
                revoked = client.get(
                    f"http://127.0.0.1:{first}/v1/admin/session",
                    headers={
                        "Origin": ORIGIN,
                        "Cookie": f"{cookie_name}={active_cookie}",
                    },
                )
                assert revoked.status_code == 401
                private_cors(revoked)
    finally:
        asyncio.run(cleanup())
