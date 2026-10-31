"""Opt-in demo responses cannot become authentication or a public content bypass."""

from uuid import UUID

from fastapi.testclient import TestClient

from app.core.config import CORSConfig, ReaderPreviewConfig, Settings
from app.entrypoints.api import create_app


def test_preview_is_disabled_by_default(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        for path in ["identity", f"metrics?ids={UUID(int=1)}"]:
            response = client.get(f"/v1/reader-preview/{path}")
            assert response.status_code == 404
            assert response.json()["success"] is False


def test_demo_identity_has_no_session_or_permissions(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={
            "reader_preview": ReaderPreviewConfig(enabled=True),
            "cors": CORSConfig(allowed_origins=["http://127.0.0.1:3000"]),
        }
    )
    with TestClient(create_app(settings)) as client:
        for persona in ["guest", "member"]:
            response = client.get(
                "/v1/reader-preview/identity",
                params={"persona": persona},
                headers={"Origin": "http://127.0.0.1:3000"},
            )
            assert response.status_code == 200
            assert response.json()["data"]["mode"] == "demo"
            assert response.json()["data"]["persona"] == persona
            assert set(response.json()["data"]) == {"mode", "persona", "display_name", "sampled_at"}
            assert "no-store" in response.headers["cache-control"]
            assert "set-cookie" not in response.headers
            assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
            assert "access-control-allow-credentials" not in response.headers
        assert client.get("/v1/reader-preview/identity?persona=admin").status_code == 422
        assert client.post("/v1/reader-preview/identity").status_code == 405
        assert client.get("/v1/reader-preview/metrics?ids=example").status_code == 422
        assert client.get("/v1/reader-preview/metrics").status_code == 422
        assert (
            client.get(
                "/v1/reader-preview/metrics", params=[("ids", str(UUID(int=n))) for n in range(21)]
            ).status_code
            == 422
        )
        # Public metrics actually depend on storage, even though identity is synthetic.
        assert client.get(f"/v1/reader-preview/metrics?ids={UUID(int=1)}").status_code == 503
