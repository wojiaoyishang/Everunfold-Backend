import json
import logging

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.process_logging import RuntimeLogFormatter
from app.core.request_ip import SavedRequestIPPolicy, ip_report
from app.entrypoints.api import create_app


def report(peer: str, source: str, fallback: str, **headers: str) -> dict[str, object]:
    scope = Request(
        {
            "type": "http",
            "client": (peer, 80),
            "headers": [
                (key.replace("_", "-").encode(), value.encode()) for key, value in headers.items()
            ],
        }
    ).scope
    policy = SavedRequestIPPolicy.model_validate(
        {"source": source, "fallback": fallback, "trusted_proxies": ["10.0.0.0/24"], "revision": 3}
    )
    return ip_report(scope, policy, True)


def test_untrusted_peer_supersedes_valid_header_values() -> None:
    observation = report("203.0.113.10", "x-real-ip", "peer", x_real_ip="192.0.2.7")
    assert observation["client_ip"] == "203.0.113.10"
    assert observation["client_ip_source"] == "peer"
    assert observation["x_real_ip"] == ["192.0.2.7"]
    assert observation["trusted_proxy"] is False


def test_forwarding_chain_and_fallback() -> None:
    observation = report(
        "::ffff:10.0.0.1",
        "forwarded",
        "x-real-ip",
        forwarded=(
            'for=192.0.2.100, for="[2001:db8::2]:443";host="note; for=192.0.2.9", for=10.0.0.2'
        ),
        x_real_ip="192.0.2.7",
    )
    assert observation["peer_ip"] == "10.0.0.1"
    assert observation["forwarded"] == ["192.0.2.100", "2001:db8::2", "10.0.0.2"]
    assert observation["client_ip"] == "2001:db8::2"
    assert observation["ip_policy_revision"] == 3

    broken = report(
        "10.0.0.1",
        "x-forwarded-for",
        "x-real-ip",
        x_forwarded_for="192.0.2.100, unknown, 10.0.0.2",
        x_real_ip="192.0.2.7",
    )
    assert broken["client_ip"] == "192.0.2.7"
    assert broken["client_ip_source"] == "x-real-ip"
    assert broken["x_forwarded_for"] == ["192.0.2.100", None, "10.0.0.2"]
    assert report("10.0.0.1", "x-forwarded-for", "none")["client_ip"] is None


def test_http_error_log_fields_omit_credentials(caplog, monkeypatch) -> None:
    monkeypatch.delenv("EVERUNFOLD_LOG_FORMAT", raising=False)
    app = create_app()
    caplog.set_level(logging.INFO, logger="uvicorn.error.everunfold.http")
    with TestClient(app, client=("203.0.113.10", 1234)) as client:
        response = client.get(
            "/missing?token=querysecret",
            headers={
                "X-Real-IP": "192.0.2.7",
                "Authorization": "Bearer bodysecret",
                "User-Agent": "Everunfold-Log-Test/1.0",
            },
        )
    assert response.status_code == 404
    records = [record for record in caplog.records if hasattr(record, "http_access")]
    assert len(records) == 1
    encoded = RuntimeLogFormatter().format(records[0])
    assert "querysecret" not in encoded and "bodysecret" not in encoded
    event = json.loads(encoded)
    assert (event["event"], event["status"], event["client_ip"], event["x_real_ip"]) == (
        "http_access",
        404,
        "203.0.113.10",
        ["192.0.2.7"],
    )
    assert event["request_url"] == "/missing"
    assert event["route"] is None
    assert event["user_agent"] == "Everunfold-Log-Test/1.0"
    assert event["outcome"] == "completed" and event["duration_ms"] >= 0
    assert len(event["request_id"]) == 32

    monkeypatch.setenv("EVERUNFOLD_LOG_FORMAT", "text")
    plain = RuntimeLogFormatter().format(records[0])
    assert "[fastapi] http_access" in plain
    assert '"client_ip": "203.0.113.10"' in plain
    assert "querysecret" not in plain and "bodysecret" not in plain
    assert "\n" not in plain
