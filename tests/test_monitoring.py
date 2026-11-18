from ipaddress import ip_network

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.monitor_config import MonitoringConfig
from app.core.monitor_metrics import Monitoring
from app.core.plugins.loader import PluginCatalog


def test_metrics_requires_token_and_actual_source_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    secret = "x" * 32
    config = MonitoringConfig(secret, (ip_network("192.0.2.4/32"),), "", "", "")
    app = FastAPI()
    app.state.runtime = None

    @app.get("/content/{slug}")
    async def article(slug: str) -> str:
        return slug

    Monitoring(config, PluginCatalog(())).install(app)
    headers = {"Authorization": f"Bearer {secret}", "X-Forwarded-For": "192.0.2.4"}
    with TestClient(app, client=("198.51.100.7", 1000)) as remote:
        assert remote.get("/metrics", headers=headers).status_code == 403
    with TestClient(app, client=("192.0.2.4", 1000)) as monitor:
        assert monitor.get("/metrics").status_code == 403
        assert monitor.get("/content/unique-article").status_code == 200
        response = monitor.get("/metrics", headers=headers)
        assert response.status_code == 200
        assert 'handler="/content/{slug}"' in response.text
        assert "unique-article" not in response.text
