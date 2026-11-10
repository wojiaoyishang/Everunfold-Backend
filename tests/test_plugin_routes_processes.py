"""Two real Uvicorn processes share dynamic plugin admission and live key ceilings."""

import asyncio
import copy
import os
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx2 as httpx
from sqlalchemy import select

from app.core.database import Database
from app.modules.content.models import SiteSettings
from app.modules.settings.backend_plugins import install_backend_plugins
from tests.test_accounts import AccountsTest, headers
from tests.test_api_keys import create_key
from tests.test_auth_http_processes import free_port, uvicorn_process
from tests.test_page_comments import comments as routes_fixture

route_database = routes_fixture


def test_plugin_routes_two_api_replicas_permissions_activation_and_deployment(
    route_database: AccountsTest, tmp_path: Path
) -> None:
    ctx = route_database
    env = {**os.environ, "CONFIG_FILE": str(tmp_path / "backend.local.toml")}
    env.pop("DATABASE_URL", None)
    env.pop("REDIS_URL", None)
    env["EVERUNFOLD_API_APPLICATION_MASTER_KEY"] = "test_" + "r" * 43
    ports = (free_port(), free_port())
    path = "/v1/plugins/example.routes"

    async def install() -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                await install_backend_plugins(session, ctx.settings)
        finally:
            await db.close()

    async def change_digest(broken: bool) -> None:
        from app.core.plugins.loader import plugin_catalog

        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                site = await session.scalar(
                    select(SiteSettings).where(SiteSettings.id == 1).with_for_update()
                )
                data = copy.deepcopy(site.data)
                for item in data["_backend_plugins"]:
                    if item["info"]["id"] == "example.routes":
                        item["info"]["build"] = (
                            "f" * 64
                            if broken
                            else next(
                                p.info.build
                                for p in plugin_catalog().plugins
                                if p.info.id == "example.routes"
                            )
                        )
                site.data = data
                await session.commit()
        finally:
            await db.close()

    with ExitStack() as stack:
        for port in ports:
            stack.enter_context(uvicorn_process(port, env))
        clients = [
            stack.enter_context(
                httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False)
            )
            for port in ports
        ]
        a, b = clients
        assert a.get(path + "/hello").status_code == 404
        asyncio.run(install())
        for client in clients:
            assert (
                client.get(path + "/hello").json()["data"]["message"]
                == "Hello from a backend plugin"
            )
            assert (
                client.get(path + "/hello/Ada?repeat=2").json()["data"]["message"]
                == "Hello, Ada Hello, Ada"
            )
            assert client.get(path + "/hello/Ada?repeat=4").status_code == 422
            assert client.get(path + "/me").status_code == 401
            echoed = client.post(path + "/echo", json={"text": "server callback"})
            assert echoed.status_code == 200 and echoed.json()["data"]["text"] == "server callback"
            assert echoed.headers["cache-control"] == "private, no-store"
            assert client.post(path + "/echo", json={"text": ""}).status_code == 422
            assert (
                client.get(
                    path + "/hello", headers={"Authorization": "Bearer efk_invalid"}
                ).status_code
                == 401
            )
        a.cookies.update(dict(ctx.root.cookies))
        assert a.get(path + "/me").json()["data"]["auth_method"] == "session"
        assert (
            a.post(
                path + "/session-echo",
                json={"text": "private"},
                headers={"Origin": "http://127.0.0.1:3000"},
            ).status_code
            == 403
        )
        assert (
            a.post(
                path + "/session-echo", json={"text": "private"}, headers=headers(ctx.root)
            ).status_code
            == 200
        )
        limited, _ = create_key(ctx)
        allowed, _ = create_key(ctx, permissions={"settings:read": "all"})
        assert (
            a.get(
                path + "/inspect", headers={"Authorization": "Bearer " + limited["secret"]}
            ).status_code
            == 403
        )
        machine = {"Authorization": "Bearer " + allowed["secret"]}
        assert b.get(path + "/inspect", headers=machine).json()["data"]["auth_method"] == "api_key"
        assert b.post(path + "/inspect", headers=machine).status_code == 200
        assert b.get(path + "/me", headers=machine).status_code == 403
        assert (
            b.post(path + "/inspect", headers={**machine, "Origin": "http://evil.test"}).status_code
            == 403
        )
        revoked = ctx.root.post(
            "/v1/account/api-keys/revoke",
            headers=headers(ctx.root),
            json={"operation_id": str(uuid4()), "key_id": allowed["key"]["id"]},
        )
        assert revoked.status_code == 200
        assert b.get(path + "/inspect", headers=machine).status_code == 401

        def toggle(disabled: bool) -> None:
            state = ctx.root.get("/v1/admin/settings").json()["data"]
            value = state["value"]
            value["extensions"]["disabled_plugins"] = ["example.routes"] if disabled else []
            result = ctx.root.patch(
                "/v1/admin/settings",
                headers=headers(ctx.root),
                json={"expected_revision": state["revision"], "value": value},
            )
            assert result.status_code == 200, result.text

        toggle(True)
        for client in clients:
            assert client.get(path + "/hello").status_code == 404
            assert client.get(path + "/me").status_code == 404
        toggle(False)
        assert b.get(path + "/hello").status_code == 200
        asyncio.run(change_digest(True))
        assert a.get(path + "/hello").status_code == 503
        asyncio.run(change_digest(False))
        assert b.get(path + "/hello").status_code == 200
        schema = a.get("/openapi.json").json()
        assert schema["paths"][path + "/inspect"]["post"]["x-everunfold-access"] == "permission"
        ids = [
            operation["operationId"]
            for p, methods in schema["paths"].items()
            if p.startswith(path)
            for operation in methods.values()
        ]
        assert len(ids) == len(set(ids))
