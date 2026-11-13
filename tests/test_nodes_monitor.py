"""The operations console keeps its own access boundary during storage outages."""

from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import NodesMonitorConfig, Settings
from app.core.runtime import Runtime
from app.modules.auth.security import hash_password
from app.plugins.nodes_monitor.plugin import mount_nodes_monitor
from app.plugins.nodes_monitor.security import COOKIE_NAME

ORIGIN = "http://127.0.0.1:8001"
PASSWORD = "isolated monitor password"


def test_monitor_requires_separate_credentials_and_loads_without_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = token_urlsafe(32)
    monkeypatch.setenv("EVERUNFOLD_NODES_SESSION_SECRET", secret)
    settings = Settings.model_validate(
        {
            "BACKEND_BASE_URL": ORIGIN,
            "database": {"url": "postgresql+asyncpg://test@127.0.0.1:1/test"},
            "redis": {"url": "redis://127.0.0.1:1"},
            "nodes_monitor": {"enabled": True, "password_hash": hash_password(PASSWORD)},
            "frontends": {
                "web-main": {
                    "base_url": "https://site.example.test",
                    "nextjs_to_fastapi_token_env": "INBOUND_TOKEN",
                    "fastapi_to_nextjs_token_env": "OUTBOUND_TOKEN",
                }
            },
        }
    )
    app = FastAPI()
    mount_nodes_monitor(app, settings)
    # HTTP handlers can still log in and report an outage when dependencies refuse connections.
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 5000)) as client:
        runtime = Runtime.create(settings)
        app.state.runtime = runtime
        try:
            login_page = client.get("/_nodes")
            assert 'data-mode="login"' in login_page.text
            assert "no-store" in login_page.headers["Cache-Control"]
            assert "web-main" not in login_page.text
            assert (
                client.get(
                    "/_nodes/status", headers={"Cookie": "everunfold_session=admin"}
                ).status_code
                == 401
            )
            assert client.post("/_nodes/login", data={"password": PASSWORD}).status_code == 403
            assert (
                client.post(
                    "/_nodes/login", data={"password": "wrong"}, headers={"Origin": ORIGIN}
                ).status_code
                == 401
            )
            success = client.post(
                "/_nodes/login",
                data={"password": PASSWORD},
                headers={"Origin": ORIGIN},
                follow_redirects=False,
            )
            assert success.status_code == 303
            assert "HttpOnly" in success.headers["Set-Cookie"]
            assert "Path=/_nodes" in success.headers["Set-Cookie"]
            assert 'data-mode="dashboard"' in client.get("/_nodes").text
            value = client.get("/_nodes/status").json()
            assert value["api"] == "online"
            assert value["database"] == value["redis"] == "unavailable"
            assert not value["history_available"]
            assert value["nodes"][0]["id"] == "web-main"
            assert not value["nodes"][0]["sync"]
            assert secret not in str(value)
            assert client.post("/_nodes/logout", headers={"Origin": ORIGIN}).status_code == 200
            assert COOKIE_NAME not in client.cookies
            assert client.get("/_nodes/status").status_code == 401
        finally:
            assert client.portal is not None
            client.portal.call(runtime.close)


def test_monitor_config_rejects_plaintext_and_disallows_nonloopback_http() -> None:
    with pytest.raises(ValidationError, match="valid password hash"):
        NodesMonitorConfig.model_validate({"password_hash": "plaintext"})
    with pytest.raises(ValidationError, match="requires HTTPS or loopback HTTP"):
        Settings.model_validate(
            {
                "BACKEND_BASE_URL": ORIGIN,
                "database": {"url": "postgresql+asyncpg://test@127.0.0.1/test"},
                "redis": {"url": "redis://127.0.0.1"},
                "nodes_monitor": {
                    "enabled": True,
                    "origin": "http://ops.example.test",
                    "password_hash": hash_password(PASSWORD),
                },
            }
        )
