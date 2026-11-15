"""Browser policy for deployment-bound frontend registrations."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.auth.middleware import AuthBoundaryMiddleware, BusinessCORSMiddleware
from app.modules.settings.browser_origins import ManagedBrowserOrigins
from app.modules.settings.models import FrontendNodeRegistry


def test_only_enabled_and_bound_nodes_share_the_browser_allowlist() -> None:
    nodes = {
        "one": {"base_url": "https://ONE.example.test:443", "enabled": True},
        "two": {"base_url": "https://two.example.test", "enabled": True},
        "disabled": {"base_url": "https://disabled.example.test", "enabled": False},
        "unbound": {"base_url": "https://unbound.example.test", "enabled": True},
    }
    binding = {
        "base_url": "https://internal.example.test",
        "nextjs_to_fastapi_token_env": "INBOUND",
        "fastapi_to_nextjs_token_env": "OUTBOUND",
    }
    record = FrontendNodeRegistry(
        id=1, revision=1, nodes=nodes, bindings=dict.fromkeys(("one", "two", "disabled"), binding)
    )

    class Session:
        async def scalar(self, query: object) -> FrontendNodeRegistry:
            return record

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[Session]:
        yield Session()

    app = FastAPI()
    cors = SimpleNamespace(
        allowed_origins=["https://legacy.example.test"], use_frontend_registry=True
    )
    app.state.runtime = SimpleNamespace(
        settings=SimpleNamespace(cors=cors),
        database=SimpleNamespace(session_factory=session_factory),
    )
    app.add_middleware(AuthBoundaryMiddleware, allowed_origins=cors.allowed_origins)
    app.add_middleware(BusinessCORSMiddleware, allowed_origins=cors.allowed_origins)
    app.add_middleware(ManagedBrowserOrigins)

    @app.post("/v1/admin/example")
    def write() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        for name in ("one", "two", "legacy", "disabled", "unbound", "internal", "other"):
            origin = f"https://{name}.example.test"
            allowed = name in {"one", "two", "legacy"}
            options = client.options(
                "/v1/admin/example",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "X-CSRF-Token",
                },
            )
            response = client.post("/v1/admin/example", headers={"Origin": origin})
            assert options.status_code == (200 if allowed else 400)
            assert response.status_code == (200 if allowed else 403)
            assert response.headers.get("access-control-allow-origin") == (
                origin if allowed else None
            )
        record.nodes = {**nodes, "two": {"base_url": "https://new.example.test", "enabled": True}}
        assert (
            client.post(
                "/v1/admin/example", headers={"Origin": "https://two.example.test"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/v1/admin/example", headers={"Origin": "https://new.example.test"}
            ).status_code
            == 200
        )
