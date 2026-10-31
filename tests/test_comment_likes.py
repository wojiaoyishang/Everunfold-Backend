"""The browser cannot fabricate a successful mutation or grant account privileges."""

from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import CORSConfig, Settings
from app.entrypoints.api import create_app


def test_likes_require_valid_visitor_and_explicit_boolean(test_settings: Settings) -> None:
    comment = uuid4()
    with TestClient(create_app(test_settings)) as client:
        assert client.get(f"/v1/comments/likes?ids={comment}").status_code == 422
        assert client.put(f"/v1/comments/{comment}/like", json={"liked": True}).status_code == 422
        headers = {"X-Everunfold-Visitor": str(uuid4())}
        for body in [{"liked": "true"}, {"liked": True, "admin": True}]:
            assert (
                client.put(f"/v1/comments/{comment}/like", json=body, headers=headers).status_code
                == 422
            )
        # A real storage failure must stay a failure, rather than returning an optimistic ACK.
        response = client.put(f"/v1/comments/{comment}/like", json={"liked": True}, headers=headers)
        assert response.status_code == 503
        assert response.json()["success"] is False


def test_likes_cors_has_no_credentials_and_rejects_foreign_origin(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={"cors": CORSConfig(allowed_origins=["https://blog.test"])}
    )
    with TestClient(create_app(settings)) as client:
        preflight = client.options(
            f"/v1/comments/{uuid4()}/like",
            headers={
                "Origin": "https://blog.test",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "Content-Type,X-Everunfold-Visitor",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://blog.test"
        assert "access-control-allow-credentials" not in preflight.headers
        response = client.put(
            f"/v1/comments/{uuid4()}/like",
            json={"liked": True},
            headers={"Origin": "https://foreign.test", "X-Everunfold-Visitor": str(uuid4())},
        )
        assert response.status_code == 403
        assert "set-cookie" not in response.headers
