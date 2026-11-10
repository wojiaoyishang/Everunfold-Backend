"""Frozen native FastAPI routes, scoped to one plugin's HTTP namespace."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from app.modules.auth.permissions import CAPABILITIES, ResourceScope

if TYPE_CHECKING:
    from app.core.plugins.registry import PluginRegistry

PARAMETER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*(?::(?:str|int|float|uuid|path))?\}")
METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
ROUTE_OPTIONS = (
    "response_model",
    "status_code",
    "tags",
    "dependencies",
    "summary",
    "description",
    "response_description",
    "responses",
    "deprecated",
    "methods",
    "operation_id",
    "response_model_include",
    "response_model_exclude",
    "response_model_by_alias",
    "response_model_exclude_unset",
    "response_model_exclude_defaults",
    "response_model_exclude_none",
    "include_in_schema",
    "response_class",
    "name",
    "callbacks",
    "openapi_extra",
    "generate_unique_id_function",
    "strict_content_type",
)


@dataclass(frozen=True)
class RoutePolicy:
    access: Literal["public", "session", "permission"] = "session"
    permission: str | None = None
    scope: ResourceScope = "all"


@dataclass(frozen=True)
class RouteContribution:
    routes: tuple[APIRoute, ...]
    policy: RoutePolicy

    def manifest(self) -> list[dict[str, object]]:
        return [
            {
                "path": route.path,
                "methods": sorted(route.methods or ()),
                "access": self.policy.access,
                "permission": self.policy.permission,
                "scope": self.policy.scope,
                "operation_id": route.operation_id,
            }
            for route in self.routes
        ]


def valid_path(path: str, *, parameters: bool = True) -> bool:
    if not path:
        return True
    if not path.startswith("/") or any(char in path for char in ("%", "\\", "?", "#")):
        return False
    for segment in path.strip("/").split("/"):
        if segment in {"", ".", ".."}:
            return path == "/"
        if parameters and PARAMETER.fullmatch(segment):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.~-]+", segment):
            return False
    return "//" not in path


def copy_route(
    target: APIRouter,
    path: str,
    source: RouteContext | APIRoute,
    *,
    route_class: type[APIRoute] | None = None,
    **overrides: Any,
) -> None:
    # FastAPI 0.162 uses lazy includes; RouteContext resolves nested prefixes and DI.
    options = {name: getattr(source, name) for name in ROUTE_OPTIONS}
    for name, value in list(options.items()):
        if isinstance(value, list):
            options[name] = list(value)
        elif isinstance(value, dict):
            options[name] = dict(value)
    options.update(overrides)
    endpoint = source.endpoint
    if endpoint is None:
        raise ValueError("Plugin API route has no endpoint")
    target.add_api_route(path, endpoint, route_class_override=route_class, **options)


class RouteRegistry:
    def __init__(self, owner: "PluginRegistry") -> None:
        self.owner = owner
        self.items: list[RouteContribution] = []
        self._operations: set[str] = set()
        self._paths: set[tuple[str, str]] = set()

    def register(
        self,
        router: APIRouter,
        *,
        prefix: str = "",
        access: Literal["public", "session", "permission"] = "session",
        permission: str | None = None,
        scope: ResourceScope = "all",
    ) -> None:
        self.owner.check_open()
        if not isinstance(router, APIRouter) or not valid_path(prefix, parameters=False):
            raise ValueError("Invalid plugin router or prefix")
        if prefix.endswith("/") or router.on_startup or router.on_shutdown:
            raise ValueError("Use Plugin startup/shutdown; router prefix must not end in a slash")
        if access not in {"public", "session", "permission"} or scope not in {"own", "all"}:
            raise ValueError("Invalid plugin route access policy")
        if (
            (access == "permission") != (permission is not None)
            or (permission is not None and permission not in CAPABILITIES)
            or (scope == "own" and (not permission or not CAPABILITIES[permission][2]))
        ):
            raise ValueError("Invalid plugin route permission")
        policy = RoutePolicy(access, permission, scope)
        frozen = APIRouter()
        paths, operations = self._paths.copy(), self._operations.copy()
        for context in iter_route_contexts(router.routes):
            source = context.original_route
            if not isinstance(source, APIRoute) or not valid_path(context.path or ""):
                raise ValueError("Only native HTTP API routes are supported by plugin registration")
            methods = context.methods or set()
            if not methods or not methods <= METHODS:
                raise ValueError("Unsupported plugin HTTP method")
            path = f"/v1/plugins/{self.owner.id}{prefix}{context.path or ''}"
            normalized = PARAMETER.sub("{}", path)
            keys = {(method, normalized) for method in methods}
            if keys & paths:
                raise ValueError(f"Duplicate plugin route: {path}")
            paths.update(keys)
            suffix = sha256(path.encode()).hexdigest()[:12]
            name = re.sub(r"[^A-Za-z0-9_]+", "_", context.name or "route")
            for method in sorted(methods):
                operation = (
                    f"{self.owner.id}.{context.operation_id}.{method.lower()}"
                    if context.operation_id
                    else f"{self.owner.id}.{name}_{method.lower()}_{suffix}"
                )
                if operation in operations:
                    raise ValueError(f"Duplicate plugin operation ID: {operation}")
                operations.add(operation)
                copy_route(
                    frozen,
                    path,
                    context,
                    route_class=type(source),
                    methods=[method],
                    name=f"{self.owner.id}:{context.name}",
                    operation_id=operation,
                    tags=[f"plugin:{self.owner.id}", *context.tags],
                    openapi_extra={
                        **(context.openapi_extra or {}),
                        "x-everunfold-plugin": self.owner.id,
                        "x-everunfold-access": access,
                        "x-everunfold-permission": permission,
                    },
                )
        if not frozen.routes:
            raise ValueError("Plugin router has no HTTP routes")
        self.items.append(RouteContribution(tuple(frozen.routes), policy))  # type: ignore[arg-type]
        self._paths, self._operations = paths, operations


def public_plugin_request(scope: Mapping[str, Any]) -> bool:
    app = scope.get("app")
    for route, policy in getattr(getattr(app, "state", None), "plugin_http_routes", ()):
        if scope.get("method") in route.methods and route.path_regex.fullmatch(
            scope.get("path", "")
        ):
            return bool(policy.access == "public")
    return False
