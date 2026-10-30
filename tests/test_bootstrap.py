"""Smoke checks for the bootstrap boundary and shared HTTP response behavior."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.responses import JSONResponseError, JSONResponseFail, JSONResponseSuccess
from app.entrypoints.api import create_app


def test_liveness_does_not_claim_readiness() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "code": 200,
            "msg": "请求成功",
            "data": {"status": "alive", "stage": "bootstrap"},
        }
        assert client.get("/health/ready").status_code == 404


@pytest.mark.parametrize("path", ["/missing", "/v1/articles", "/api/v1/articles"])
def test_unimplemented_routes_are_not_fake_business_endpoints(path: str) -> None:
    with TestClient(create_app()) as client:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"success": False, "code": 404, "msg": "资源不存在"}


def test_method_error_keeps_http_status_and_headers() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/health/live")
        assert response.status_code == 405
        assert "GET" in response.headers["allow"]
        assert response.json()["code"] == 405


def test_validation_and_server_error_responses_do_not_leak_input() -> None:
    application = create_app()

    @application.get("/test/validated")
    async def validated(value: int) -> dict[str, int]:
        return {"value": value}

    @application.get("/test/error")
    async def failure() -> None:
        raise RuntimeError("secret-example-must-not-appear")

    with TestClient(application, raise_server_exceptions=False) as client:
        invalid = client.get("/test/validated?value=private-input")
        assert invalid.status_code == 422
        assert invalid.json() == {"success": False, "code": 422, "msg": "请求参数无效"}
        failure_response = client.get("/test/error")
        assert failure_response.status_code == 500
        assert failure_response.json() == {"success": False, "code": 500, "msg": "处理失败"}


def test_response_helpers_keep_status_business_code_and_serialization() -> None:
    response = JSONResponseSuccess(
        data={"created_at": datetime(2026, 10, 30, tzinfo=UTC)},
        status_code=201,
        headers={"X-Request-ID": "test-request"},
    )
    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == "test-request"
    created_at = json.loads(bytes(response.body))["data"]["created_at"]
    assert datetime.fromisoformat(created_at) == datetime(2026, 10, 30, tzinfo=UTC)
    failure = JSONResponseFail(msg="文章不存在", code=40401, status_code=404)
    assert failure.success is False
    assert failure.code == 40401
    assert failure.data is None
    assert json.loads(bytes(failure.body)) == {
        "success": False,
        "code": 40401,
        "msg": "文章不存在",
    }
    assert JSONResponseError().status_code == 500
