"""API-only route assembly and request-owned transactions. No application in setup."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.plugins.route_auth import route_actor
from app.core.plugins.routes import RoutePolicy, copy_route
from app.core.plugins.state import enabled_ids, installed_info
from app.core.runtime import Runtime
from app.core.schemas import ErrorResponse
from app.modules.auth.permissions import Actor
from app.modules.auth.router import auth_runtime
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings

if TYPE_CHECKING:
    from app.core.plugins.loader import LoadedPlugin, PluginCatalog


@dataclass(frozen=True)
class PluginRequestContext:
    plugin_id: str
    runtime: Runtime
    session: AsyncSession
    actor: Actor | None


async def plugin_request(request: Request) -> PluginRequestContext:
    context = getattr(request.state, "plugin_context", None)
    if not isinstance(context, PluginRequestContext):
        raise RuntimeError("PluginRequest must be used inside a registered plugin route")
    return context


PluginRequest = Annotated[PluginRequestContext, Depends(plugin_request)]


def available(data: dict[str, Any], owner: str, catalog: "PluginCatalog") -> None:
    installed = {item.id: item for item in installed_info(data)}
    if owner not in enabled_ids(data):
        raise AuthFailure(404, 40489, "插件接口不可用")
    local = {item.info.id: item.info for item in catalog.plugins}
    pending, checked = [owner], set()
    while pending:
        identity = pending.pop()
        if identity in checked:
            continue
        checked.add(identity)
        current, applied = local.get(identity), installed.get(identity)
        if not current or not applied or current.build != applied.build:
            raise AuthFailure(503, 50389, "插件部署尚未同步")
        pending.extend(current.requires)


def guarded_route(
    base: type[APIRoute], owner: "LoadedPlugin", policy: RoutePolicy, catalog: "PluginCatalog"
) -> type[APIRoute]:
    class GuardedPluginRoute(base):  # type: ignore[valid-type,misc]
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            original = super().get_route_handler()

            async def guarded(request: Request) -> Response:
                runtime = auth_runtime(request)
                async with runtime.database.session_factory() as session:
                    site = await session.get(SiteSettings, 1)
                    available(site.data if site else {}, owner.info.id, catalog)
                    actor = await route_actor(request, session, runtime, policy)
                    if policy.media_references:
                        from app.modules.media.locking import lock_references

                        await lock_references(session)
                    site = await session.scalar(
                        select(SiteSettings)
                        .where(SiteSettings.id == 1)
                        .with_for_update(read=not policy.media_references)
                        .execution_options(populate_existing=True)
                    )
                    available(site.data if site else {}, owner.info.id, catalog)
                    request.state.plugin_context = PluginRequestContext(
                        owner.info.id, runtime, session, actor
                    )
                    try:
                        response: Response = await original(request)
                        if response.status_code < 400:
                            await session.commit()
                        else:
                            await session.rollback()
                        return response
                    finally:
                        del request.state.plugin_context

            return guarded

    return GuardedPluginRoute


def mount_plugin_routes(application: FastAPI, catalog: "PluginCatalog") -> None:
    # Construct the complete isolated router before changing application routes.
    assembled = APIRouter()
    declarations: list[tuple[APIRoute, RoutePolicy]] = []
    for owner in catalog.plugins:
        for contribution in owner.routes:
            for route in contribution.routes:
                copy_route(
                    assembled,
                    route.path,
                    route,
                    route_class=guarded_route(type(route), owner, contribution.policy, catalog),
                    responses={
                        **route.responses,
                        **{
                            status: {"model": ErrorResponse}
                            for status in (401, 403, 404, 413, 422, 500, 503)
                        },
                    },
                )
                declarations.append((route, contribution.policy))
    application.state.plugin_http_routes = tuple(declarations)
    if declarations:
        application.include_router(assembled)
