"""Native HTTP route declarations and real PostgreSQL admission/transaction boundaries."""

import asyncio

import pytest
from fastapi import APIRouter, Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import Database
from app.core.plugins.definition import define_plugin
from app.core.plugins.loader import collect_plugins
from app.core.plugins.registry import PluginRegistry
from app.core.plugins.route_runtime import available
from app.core.plugins.routes import valid_path
from app.entrypoints.api import create_app
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.settings.models import SettingsAudit
from app.plugin_sdk import JSONResponseFail, JSONResponseSuccess, PluginRequest
from tests.test_accounts import ORIGIN, AccountsTest, headers
from tests.test_page_comments import comments as route_database  # noqa: F401


def test_routes_are_frozen_namespaced_and_keep_native_nested_dependencies() -> None:
    route = APIRouter()

    @route.api_route("/items/{identity:int}", methods=["GET", "PUT"], operation_id="item")
    async def item(identity: int) -> dict[str, int]:
        return {"identity": identity}

    parent = APIRouter()
    parent.include_router(route, prefix="/nested", dependencies=[Depends(lambda: None)])
    retained = []

    def setup(registry: PluginRegistry) -> None:
        retained.append(registry)
        registry.routes.register(parent, prefix="/api", access="public")

    plugin = define_plugin(id="test.routes", title="Routes", setup=setup)
    catalog = collect_plugins([(plugin, "0" * 64)])
    routes = catalog.plugins[0].routes[0].routes
    assert len(routes) == 2
    assert {x.path for x in routes} == {"/v1/plugins/test.routes/api/nested/items/{identity:int}"}
    assert {x.operation_id for x in routes} == {"test.routes.item.get", "test.routes.item.put"}
    assert all(len(x.dependencies) == 1 for x in routes)
    route.add_api_route("/late", lambda: {})
    parent.routes.clear()
    assert len(catalog.plugins[0].routes[0].routes) == 2
    with pytest.raises(RuntimeError, match="finished"):
        retained[0].routes.register(route)
    changed = collect_plugins(
        [(define_plugin(id="test.routes", title="Routes", setup=lambda r: None), "0" * 64)]
    )
    assert catalog.plugins[0].info.build != changed.plugins[0].info.build


@pytest.mark.parametrize("path", ["/../x", "/a/./b", "/a//b", "/%2e/x", "/a?b", "/x#y", "/x\\y"])
def test_routes_reject_noncanonical_paths(path: str) -> None:
    assert not valid_path(path)
    registry = PluginRegistry("test.routes")
    router = APIRouter()
    router.add_api_route("/ok", lambda: {})
    with pytest.raises(ValueError):
        registry.routes.register(router, prefix=path)


def test_routes_reject_ambiguous_operations_websockets_and_invalid_policies() -> None:
    router = APIRouter()
    router.add_api_route("/x/{id}", lambda id: {})
    registry = PluginRegistry("test.routes")
    registry.routes.register(router)
    conflict = APIRouter()
    conflict.add_api_route("/x/{name:int}", lambda name: {})
    with pytest.raises(ValueError, match="Duplicate plugin route"):
        registry.routes.register(conflict)
    with pytest.raises(ValueError, match="permission"):
        registry.routes.register(router, permission="settings:read")
    with pytest.raises(ValueError, match="permission"):
        registry.routes.register(router, access="permission", permission="made-up:admin")
    socket = APIRouter()
    socket.add_api_websocket_route("/socket", lambda: None)
    with pytest.raises(ValueError, match="native HTTP"):
        PluginRegistry("test.socket").routes.register(socket)
    operations = APIRouter()
    operations.add_api_route("/a", lambda: {}, operation_id="same")
    operations.add_api_route("/b", lambda: {}, operation_id="same")
    with pytest.raises(ValueError, match="operation ID"):
        PluginRegistry("test.operations").routes.register(operations)


def test_route_availability_requires_current_owner_and_dependency_builds() -> None:
    first = define_plugin(id="test.first", title="First", setup=lambda r: None)
    second = define_plugin(
        id="test.second", title="Second", setup=lambda r: None, requires=(first.id,)
    )
    catalog = collect_plugins([(first, "0" * 64), (second, "1" * 64)])
    data = {"_backend_plugins": catalog.snapshot()}
    available(data, second.id, catalog)
    data["_backend_plugins"][0]["info"]["build"] = "f" * 64
    with pytest.raises(AuthFailure) as mismatch:
        available(data, second.id, catalog)
    assert mismatch.value.status == 503
    data["extensions"] = {"disabled_plugins": [first.id]}
    with pytest.raises(AuthFailure) as disabled:
        available(data, second.id, catalog)
    assert disabled.value.status == 404


def test_plugin_native_http_admission_and_transaction_rollback(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx: AccountsTest = request.getfixturevalue("route_database")
    router = APIRouter()
    public = APIRouter()
    calls: list[str] = []

    async def dependency(request: Request) -> None:
        calls.append(request.url.path)

    @router.api_route(
        "/tx/{status:int}",
        methods=["POST", "PUT", "PATCH", "DELETE"],
        response_model=None,
        dependencies=[Depends(dependency)],
    )
    async def tx(status: int, ctx: PluginRequest) -> JSONResponseSuccess | JSONResponseFail:
        ctx.session.add(
            SettingsAudit(
                user_id=ctx.actor.id, resource="plugin-probe", revision=status, changes={}
            )
        )
        if status == 409:
            return JSONResponseFail(status_code=409)
        if status == 500:
            raise RuntimeError("rollback probe")
        return JSONResponseSuccess(data={"plugin": ctx.plugin_id, "status": status})

    @public.post("/callback")
    async def callback(request: Request, ctx: PluginRequest) -> JSONResponseSuccess:
        assert ctx.actor is None
        return JSONResponseSuccess(data={"body": (await request.body()).decode()})

    def setup(registry: PluginRegistry) -> None:
        registry.routes.register(router)
        registry.routes.register(public, access="public")

    catalog = collect_plugins(
        [(define_plugin(id="test.http", title="HTTP", setup=setup), "0" * 64)]
    )
    monkeypatch.setattr("app.entrypoints.api.plugin_catalog", lambda: catalog)

    async def deployed() -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                site = await session.get(SiteSettings, 1)
                site.data = {**site.data, "_backend_plugins": catalog.snapshot()}
                await session.commit()
        finally:
            await db.close()

    asyncio.run(deployed())
    with TestClient(
        create_app(ctx.settings), client=(ctx.peer, 50002), raise_server_exceptions=False
    ) as client:
        base = "/v1/plugins/test.http"
        assert (
            client.post(base + "/callback", content=b"raw signed payload").json()["data"]["body"]
            == "raw signed payload"
        )
        assert client.post(base + "/tx/200", headers={"Origin": ORIGIN}).status_code == 401
        assert calls == []  # Host admission precedes native/plugin dependencies.
        client.cookies.update(ctx.root.cookies)
        assert client.post(base + "/tx/200", headers={"Origin": ORIGIN}).status_code == 403
        assert calls == []
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = client.request(method, base + "/tx/200", headers=headers(ctx.root))
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
        assert client.post(base + "/tx/409", headers=headers(ctx.root)).status_code == 409
        assert client.post(base + "/tx/500", headers=headers(ctx.root)).status_code == 500
        assert client.get(base + "/tx/200").status_code == 405
        assert client.post(base + "/callback", content=b"x" * 65537).status_code == 413
        assert (
            client.post(base + "/callback", headers={"Origin": "http://evil.test"}).status_code
            == 403
        )
        cors = client.options(
            base + "/tx/200",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "X-CSRF-Token, X-Plugin-Custom",
            },
        )
        assert cors.status_code == 200
        assert cors.headers["access-control-allow-origin"] == ORIGIN
        assert cors.headers["access-control-allow-credentials"] == "true"

    async def rows() -> int:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                return int(
                    await session.scalar(
                        select(func.count())
                        .select_from(SettingsAudit)
                        .where(SettingsAudit.resource == "plugin-probe")
                    )
                    or 0
                )
        finally:
            await db.close()

    assert asyncio.run(rows()) == 4


def test_plugin_openapi_is_exportable_without_a_runtime_and_has_standard_errors() -> None:
    from tools.export_plugin_routes import build_schema

    result = build_schema("example.routes")
    assert result["x-everunfold-plugin"] == "example.routes"
    assert all(path.startswith("/v1/plugins/example.routes/") for path in result["paths"])
    inspect = result["paths"]["/v1/plugins/example.routes/inspect"]
    assert inspect["get"]["operationId"] != inspect["post"]["operationId"]
    assert inspect["get"]["responses"]["401"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ErrorResponse")
    assert "Greeting" in result["components"]["schemas"]
    with pytest.raises(ValueError):
        build_schema("missing.plugin")


def test_plugin_disable_serializes_with_an_active_request(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    ctx: AccountsTest = request.getfixturevalue("route_database")
    entered = threading.Event()
    router = APIRouter()

    @router.get("/hold")
    async def hold() -> JSONResponseSuccess:
        entered.set()
        await asyncio.sleep(0.4)
        return JSONResponseSuccess(data={"finished": True})

    catalog = collect_plugins(
        [
            (
                define_plugin(
                    id="test.hold",
                    title="Hold",
                    setup=lambda registry: registry.routes.register(router, access="public"),
                ),
                "0" * 64,
            )
        ]
    )
    monkeypatch.setattr("app.entrypoints.api.plugin_catalog", lambda: catalog)

    async def change(disable: bool) -> None:
        db = Database(ctx.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                site = await session.scalar(
                    select(SiteSettings).where(SiteSettings.id == 1).with_for_update()
                )
                site.data = {
                    **site.data,
                    "_backend_plugins": catalog.snapshot(),
                    "extensions": {"disabled_plugins": ["test.hold"] if disable else []},
                }
                await session.commit()
        finally:
            await db.close()

    asyncio.run(change(False))
    with TestClient(create_app(ctx.settings)) as client, ThreadPoolExecutor(2) as pool:
        running = pool.submit(client.get, "/v1/plugins/test.hold/hold")
        assert entered.wait(3)
        disabling = pool.submit(asyncio.run, change(True))
        time.sleep(0.08)
        assert not disabling.done()
        assert running.result(5).status_code == 200
        disabling.result(5)
        assert client.get("/v1/plugins/test.hold/hold").status_code == 404
