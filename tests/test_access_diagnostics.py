"""Rejected requests explain deployment errors only in protected process logs."""

import json
import logging
from secrets import token_urlsafe
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import access_diagnostics
from app.core.config import InternalAuthConfig, Settings
from app.entrypoints.api import create_app
from app.modules.auth.security import hash_password
from app.modules.settings import extensions_router, sync_access
from app.modules.settings.node_registry import NodeBinding
from app.plugins.nodes_monitor.plugin import mount_nodes_monitor


def events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage().split("[Everunfold access] ", 1)[1])
        for record in caplog.records
        if "[Everunfold access] " in record.getMessage()
    ]


def test_monitor_logs_effective_grants_and_proxy_failure_without_credentials(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    access_diagnostics._recent.clear()
    caplog.set_level(logging.INFO, logger=access_diagnostics.logger.name)
    secret = token_urlsafe(32)
    monkeypatch.setenv("EVERUNFOLD_NODES_SESSION_SECRET", secret)
    values = test_settings.model_dump()
    values["auth"]["cookie_secure"] = True
    values.update(
        BACKEND_BASE_URL="https://api.example.test",
        nodes_monitor={
            "enabled": True,
            "password_hash": hash_password("isolated console password"),
            "trusted_tls_proxy_ips": ["192.0.2.10"],
        },
        internal_auth={"allowed_peer_ips": ["192.0.2.0/24"]},
    )
    settings = Settings.model_validate(values)
    app = FastAPI()
    mount_nodes_monitor(app, settings)
    assert events(caplog)[0]["allowed_peer_ips"] == ["192.0.2.0/24"]
    with TestClient(app, base_url="http://api.example.test", client=("192.0.2.11", 2)) as client:
        for _ in range(2):
            response = client.get(
                "/_nodes?password=private-query",
                headers={"X-Forwarded-Proto": "https", "Cookie": "secret-cookie"},
            )
            assert response.status_code == 404
            assert "peer_ip" not in response.text
    rejected = [event for event in events(caplog) if event["code"] == "monitor_proxy_untrusted"]
    assert len(rejected) == 1
    assert rejected[0]["peer_ip"] == "192.0.2.11"
    assert rejected[0]["trusted_tls_proxy_ips"] == ["192.0.2.10"]
    with TestClient(app, base_url="http://api.example.test", client=("192.0.2.10", 2)) as client:
        assert client.get("/_nodes").status_code == 404
        assert events(caplog)[-1]["code"] == "monitor_https_required"
        assert client.get("/_nodes", headers={"X-Forwarded-Proto": "https"}).status_code == 200
    for private in (secret, "private-query", "secret-cookie", "isolated console password"):
        assert private not in caplog.text
    settings.nodes_monitor.enabled = False
    mount_nodes_monitor(FastAPI(), settings)
    assert events(caplog)[-1]["code"] == "monitor_disabled"


@pytest.mark.parametrize(
    ("code", "peer", "registered", "enabled", "token", "supplied"),
    [
        ("peer_not_allowed", "198.51.100.2", True, True, "private-token", "Bearer private-token"),
        ("token_missing", "127.0.0.1", True, True, "private-token", ""),
        ("node_not_registered", "127.0.0.1", False, True, "private-token", "Bearer private-token"),
        ("node_disabled", "127.0.0.1", True, False, "private-token", "Bearer private-token"),
        ("token_unconfigured", "127.0.0.1", True, True, "", "Bearer private-token"),
        ("token_mismatch", "127.0.0.1", True, True, "private-token", "Bearer wrong-token"),
    ],
)
def test_inventory_rejections_distinguish_node_state_ip_and_tokens(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    code: str,
    peer: str,
    registered: bool,
    enabled: bool,
    token: str,
    supplied: str,
) -> None:
    access_diagnostics._recent.clear()
    target = NodeBinding(
        base_url="https://web.example.test",
        enabled=enabled,
        nextjs_to_fastapi_token_env="DIAGNOSTIC_TEST_TOKEN",
        fastapi_to_nextjs_token_env="DIAGNOSTIC_REVERSE_TOKEN",
    )
    settings = test_settings.model_copy(
        update={"internal_auth": InternalAuthConfig(allowed_peer_ips=["127.0.0.1/32"])}
    )
    monkeypatch.setenv("DIAGNOSTIC_TEST_TOKEN", token)
    record = AsyncMock()
    monkeypatch.setattr(sync_access, "record", record)
    monkeypatch.setattr(
        extensions_router,
        "read_targets",
        AsyncMock(return_value={"web": target} if registered else {}),
    )
    with TestClient(create_app(settings), client=(peer, 2)) as client:
        response = client.post(
            "/internal/extensions/web?key=private-query",
            headers={"Authorization": supplied, "X-Forwarded-For": "127.0.0.1"},
            json={"private": "private-body"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == 40320
    assert code not in response.text
    diagnostic = events(caplog)[-1]
    assert diagnostic["code"] == code
    assert diagnostic["peer_ip"] == peer
    assert diagnostic["node_id"] == "web"
    record.assert_awaited_once()
    assert record.await_args is not None
    visit: sync_access.SyncVisit = record.await_args.args[1]
    assert (visit.peer_ip, visit.reason_code, visit.status, visit.error_code) == (
        peer,
        code,
        403,
        40320,
    )
    assert visit.token_check == {"token_missing": "missing", "token_mismatch": "mismatch"}.get(
        code, "unchecked"
    )
    for private in ("private-query", "private-token", "wrong-token", "private-body"):
        assert private not in caplog.text
        assert private not in visit.model_dump_json()
