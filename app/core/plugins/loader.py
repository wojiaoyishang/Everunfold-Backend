"""Only app/plugins/*/plugin.py is discovered; other files belong to the author."""

import hashlib
import importlib
import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from app.core.jobs.definitions import TaskDefinition
from app.core.plugins.definition import Plugin, PluginRuntime
from app.core.plugins.registry import PluginRegistry
from app.core.plugins.routes import RouteContribution
from app.modules.captcha.providers import CaptchaProvider
from app.modules.notifications.platforms import NotificationPlatform
from app.modules.settings.extensions_schemas import BackendPluginInfo
from app.modules.shortcodes.schemas import ShortcodeItem


@dataclass(frozen=True)
class LoadedPlugin:
    routes: tuple[RouteContribution, ...]
    tasks: tuple[TaskDefinition, ...]
    definition: Plugin
    info: BackendPluginInfo
    shortcodes: tuple[ShortcodeItem, ...]
    captcha: tuple[CaptchaProvider, ...]
    notifications: tuple[NotificationPlatform, ...]
    locales: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class PluginCatalog:
    plugins: tuple[LoadedPlugin, ...]

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "info": item.info.model_dump(mode="json"),
                "shortcodes": [code.model_dump(mode="json") for code in item.shortcodes],
            }
            for item in self.plugins
        ]

    def provider_owner(self, identity: str) -> str | None:
        return next(
            (
                item.info.id
                for item in self.plugins
                if any(provider.id == identity for provider in item.captcha)
            ),
            None,
        )

    def notification_platform(self, identity: str) -> NotificationPlatform | None:
        return next(
            (
                platform
                for item in self.plugins
                for platform in item.notifications
                if platform.id == identity
            ),
            None,
        )

    @asynccontextmanager
    async def lifespan(self, context: PluginRuntime) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for item in self.plugins:
                stack.push_async_callback(item.definition.shutdown, context)
                await item.definition.startup(context)
            yield


def collect_plugins(entries: Iterable[tuple[Plugin, str]]) -> PluginCatalog:
    pending: dict[str, tuple[Plugin, BackendPluginInfo]] = {}
    for plugin, digest in entries:
        if not isinstance(plugin, Plugin) or plugin.api_version != 1:
            raise ValueError("Unsupported backend plugin interface")
        info = BackendPluginInfo(
            id=plugin.id,
            title=plugin.title,
            version=plugin.version,
            description=plugin.description,
            author=plugin.author,
            homepage=plugin.homepage,
            license=plugin.license,
            preview=plugin.preview,
            readme=plugin.readme,
            requires=list(plugin.requires),
            build=digest,
        )
        if info.id in pending:
            raise ValueError(f"Duplicate backend plugin: {info.id}")
        pending[info.id] = plugin, info
    completed: list[LoadedPlugin] = []
    ids: set[str] = set()
    names: set[str] = set()
    providers: set[str] = set()
    notification_platforms: set[str] = set()
    while pending:
        ready = sorted(
            key
            for key, (_, info) in pending.items()
            if all(dep in {p.info.id for p in completed} for dep in info.requires)
        )
        if not ready:
            raise ValueError(
                "Missing or cyclic backend plugin dependencies: " + ", ".join(sorted(pending))
            )
        for key in ready:
            plugin, info = pending.pop(key)
            registry = PluginRegistry(key)
            try:
                result = cast(Callable[[PluginRegistry], object], plugin.setup)(registry)
                if result is not None:
                    if inspect.iscoroutine(result):
                        result.close()
                    raise ValueError(f"Plugin setup must be synchronous: {key}")
            finally:
                registry.close()
            for code in registry.shortcodes._items:
                if code.id in ids or code.source.name in names:
                    raise ValueError(f"Duplicate backend shortcode: {code.source.name}")
                ids.add(code.id)
                names.add(code.source.name)
            for provider in registry.captcha._items:
                if provider.id in providers:
                    raise ValueError(f"Duplicate CAPTCHA provider: {provider.id}")
                providers.add(provider.id)
            for platform in registry.notifications.items:
                if platform.id in notification_platforms:
                    raise ValueError(f"Duplicate notification platform: {platform.id}")
                notification_platforms.add(platform.id)
            info.tasks = [item.id for item in registry.tasks.items]
            info.shortcodes = [item.source.name for item in registry.shortcodes._items]
            info.captcha_providers = [item.id for item in registry.captcha._items]
            info.notification_platforms = [item.id for item in registry.notifications.items]
            # Widget names are references to frontend contributions, not backend UI ownership.
            info.widgets = [item.widget for item in registry.captcha._items]
            fingerprint: dict[str, Any] = {
                "source": info.build,
                "info": info.model_dump(mode="json"),
                "shortcodes": [item.model_dump(mode="json") for item in registry.shortcodes._items],
                "locales": registry.locales._items,
                "notification_platforms": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "description": item.description,
                        "kind": item.kind,
                        "delivery_semantics": item.delivery_semantics,
                    }
                    for item in registry.notifications.items
                ],
            }
            if registry.routes.items:
                fingerprint["routes"] = [item.manifest() for item in registry.routes.items]
            info.build = hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True).encode()
            ).hexdigest()
            completed.append(
                LoadedPlugin(
                    tuple(registry.routes.items),
                    tuple(registry.tasks.items),
                    plugin,
                    info,
                    tuple(registry.shortcodes._items),
                    tuple(registry.captcha._items),
                    tuple(registry.notifications.items),
                    MappingProxyType(
                        {
                            locale: MappingProxyType(messages.copy())
                            for locale, messages in registry.locales._items.items()
                        }
                    ),
                )
            )
    return PluginCatalog(tuple(completed))


def directory_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(directory.rglob("*")):
        relative = file.relative_to(directory)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if file.is_symlink():
            raise ValueError("Backend plugin resources must not be symlinks")
        if file.is_file():
            digest.update(relative.as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def discover_plugins(root: Path, package: str = "app.plugins") -> PluginCatalog:
    entries: list[tuple[Plugin, str]] = []
    for entry in sorted(root.glob("*/plugin.py")):
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]*", entry.parent.name)
            or entry.is_symlink()
            or entry.parent.is_symlink()
        ):
            raise ValueError("Invalid backend plugin directory")
        module = importlib.import_module(f"{package}.{entry.parent.name}.plugin")
        plugin = getattr(module, "plugin", None)
        if not isinstance(plugin, Plugin):
            raise ValueError(f"Entry must export a Plugin object: {entry.parent.name}")
        entries.append((plugin, directory_digest(entry.parent)))
    return collect_plugins(entries)


@lru_cache(maxsize=1)
def plugin_catalog() -> PluginCatalog:
    return discover_plugins(Path(__file__).resolve().parents[2] / "plugins")
