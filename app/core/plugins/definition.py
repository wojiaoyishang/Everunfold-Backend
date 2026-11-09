"""Function and class plugins share one lifecycle and declaration interface."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.core.plugins.registry import PluginRegistry
    from app.core.runtime import Runtime


@dataclass(frozen=True)
class PluginRuntime:
    role: Literal["api"]
    runtime: "Runtime | None"


class Plugin:
    id: str = ""
    title: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    api_version: int = 1
    requires: tuple[str, ...] = ()

    def setup(self, registry: "PluginRegistry") -> None:
        """Contribute definitions synchronously, without request state or external I/O."""

    async def startup(self, context: PluginRuntime) -> None:
        """Initialize process resources; activation is checked separately per operation."""

    async def shutdown(self, context: PluginRuntime) -> None:
        """Release process resources, including after a partially failed startup."""


class _FunctionPlugin(Plugin):
    def __init__(
        self,
        *,
        id: str,
        title: str,
        version: str,
        description: str,
        author: str,
        requires: tuple[str, ...],
        setup: Callable[["PluginRegistry"], None],
        startup: Callable[[PluginRuntime], Awaitable[None]] | None,
        shutdown: Callable[[PluginRuntime], Awaitable[None]] | None,
    ) -> None:
        self.id, self.title, self.version = id, title, version
        self.description, self.author, self.requires = description, author, requires
        self._setup, self._startup, self._shutdown = setup, startup, shutdown

    def setup(self, registry: "PluginRegistry") -> None:
        return self._setup(registry)

    async def startup(self, context: PluginRuntime) -> None:
        if self._startup:
            await self._startup(context)

    async def shutdown(self, context: PluginRuntime) -> None:
        if self._shutdown:
            await self._shutdown(context)


def define_plugin(
    *,
    id: str,
    title: str,
    setup: Callable[["PluginRegistry"], None],
    version: str = "1.0.0",
    description: str = "",
    author: str = "",
    requires: tuple[str, ...] = (),
    startup: Callable[[PluginRuntime], Awaitable[None]] | None = None,
    shutdown: Callable[[PluginRuntime], Awaitable[None]] | None = None,
) -> Plugin:
    return _FunctionPlugin(
        id=id,
        title=title,
        version=version,
        description=description,
        author=author,
        requires=requires,
        setup=setup,
        startup=startup,
        shutdown=shutdown,
    )
