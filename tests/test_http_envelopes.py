"""All application HTTP errors keep their status, envelope and protocol headers."""

from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.entrypoints.api import create_app
from app.modules.auth.service import AuthFailure

ROOT_MESSAGE = "当我离去的时候，让我的思想来到你的身边，正如那夕阳的余晖，映在寂静星空的边缘。"


def assert_envelope(response, status: int) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert {"success", "code", "msg"} <= body.keys() <= {"success", "code", "msg", "data"}
    assert body["success"] is (status < 400)
    assert type(body["code"]) is int and isinstance(body["msg"], str)
    assert "private-detail" not in response.text
    return body


def error_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/test/http/{status}")
    async def http_error(status: int) -> None:
        raise HTTPException(status, "private-detail", headers={"Retry-After": "7"})

    @app.get("/test/storage")
    async def storage_error() -> None:
        raise SQLAlchemyError("private-detail")

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("private-detail")

    @app.get("/test/invalid-response", response_model=int)
    async def invalid_response() -> str:
        return "private-detail"

    @app.get("/test/throttle")
    async def throttle() -> None:
        raise AuthFailure(429, 42901, "请求失败", retry_after=7)

    @app.get("/test/raw/{status}")
    async def raw(status: int) -> Response:
        return PlainTextResponse("private-detail", status, {"Retry-After": "7"})

    @app.get("/test/page")
    async def page() -> HTMLResponse:
        return HTMLResponse("<p>page</p>")

    @app.get("/test/empty/{status}")
    async def empty(status: int) -> Response:
        return Response(status_code=status)

    return app


def test_root_and_disabled_documentation(test_settings: Settings) -> None:
    app = create_app(test_settings)
    assert app.docs_url is app.redoc_url is app.openapi_url is None
    # Offline contract generation is independent of publicly mounted documentation.
    assert "/v1/contents" in app.openapi()["paths"]
    with TestClient(app) as client:
        root = assert_envelope(client.get("/", headers={"Accept-Language": "en"}), 200)
        assert root == {"success": True, "code": 200, "msg": ROOT_MESSAGE}
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/unknown"):
            assert assert_envelope(client.get(path), 404)["code"] == 404


def test_framework_storage_validation_and_unhandled_errors(test_settings: Settings) -> None:
    with TestClient(error_app(test_settings), raise_server_exceptions=False) as client:
        for status in HTTPStatus:
            if status.value >= 400:
                response = client.get(f"/test/http/{status.value}")
                assert assert_envelope(response, status.value)["code"] == status.value
                assert response.headers["Retry-After"] == "7"
        for path, status in (
            ("/test/http/not-an-int", 422),
            ("/test/storage", 503),
            ("/test/crash", 500),
            ("/test/invalid-response", 500),
        ):
            assert_envelope(client.get(path), status)
        throttle = client.get("/test/throttle")
        assert assert_envelope(throttle, 429)["code"] == 42901
        assert throttle.headers["Retry-After"] == "7"
        method = client.post("/health/live")
        assert_envelope(method, 405)
        assert "GET" in method.headers["Allow"]


def test_non_json_errors_are_wrapped_without_changing_files_or_empty_responses(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    app = error_app(test_settings)
    original = tmp_path / "example.bin"
    original.write_bytes(b"0123456789")

    @app.get("/test/file")
    async def file() -> FileResponse:
        return FileResponse(original)

    with TestClient(app) as client:
        for status in (400, 401, 403, 404, 413, 429, 500, 502, 503, 504):
            response = client.get(f"/test/raw/{status}")
            assert_envelope(response, status)
            assert response.headers["Retry-After"] == "7"
            assert int(response.headers["content-length"]) == len(response.content)
        assert client.get("/test/page").text == "<p>page</p>"
        for status in (204, 304):
            assert client.get(f"/test/empty/{status}").content == b""
        assert client.get("/test/file").content == original.read_bytes()
        partial = client.get("/test/file", headers={"Range": "bytes=0-2"})
        assert partial.status_code == 206 and partial.content == b"012"
        invalid = client.get("/test/file", headers={"Range": "bytes=99-100"})
        assert_envelope(invalid, 416)
        assert invalid.headers["Content-Range"] == "bytes */10"


@pytest.mark.parametrize("path", ["/v1/auth/login", "/v1/plugins/example.routes/echo", "/v1/site"])
def test_cors_preflight_and_private_boundary_errors(test_settings: Settings, path: str) -> None:
    origin = "https://site.example.test"
    settings = test_settings.model_copy(
        update={
            "cors": test_settings.cors.model_copy(update={"allowed_origins": [origin]}),
        }
    )
    with TestClient(create_app(settings)) as client:
        method = "GET" if path == "/v1/site" else "POST"
        for requested_origin, requested_method, status in (
            (origin, method, 200),
            ("https://denied.example.test", method, 400),
            (origin, "TRACE", 400),
        ):
            response = client.options(
                path,
                headers={
                    "Origin": requested_origin,
                    "Access-Control-Request-Method": requested_method,
                },
            )
            assert_envelope(response, status)
            assert response.headers.get("Access-Control-Allow-Origin") == (
                origin if requested_origin == origin else None
            )
        if path == "/v1/auth/login":
            for headers, content, status in (
                ({"Origin": "https://denied.example.test"}, b"{}", 403),
                ({"Origin": origin, "Content-Type": "text/plain"}, b"{}", 415),
                ({"Origin": origin, "Content-Type": "application/json"}, b"x" * 17000, 413),
                ({"Origin": origin, "Content-Type": "application/json"}, b"{broken", 422),
            ):
                response = client.post(path, headers=headers, content=content)
                assert_envelope(response, status)
                assert "no-store" in response.headers["Cache-Control"]
