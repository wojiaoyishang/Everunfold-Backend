"""Scoped setup APIs. Registrations are validated before publishing a complete catalog."""

import inspect
import re
from collections.abc import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.jobs.definitions import TaskDefinition
from app.core.plugins.routes import RouteRegistry
from app.modules.captcha.providers import CaptchaProvider
from app.modules.shortcodes.compiler import compile_shortcode
from app.modules.shortcodes.schemas import ShortcodeItem, ShortcodeSource


class ShortcodeRegistry:
    def __init__(self, owner: "PluginRegistry") -> None:
        self._owner = owner
        self._items: list[ShortcodeItem] = []

    def register(
        self, source: ShortcodeSource, *, identity: str | None = None, revision: int = 1
    ) -> None:
        self._owner.check_open()
        source = ShortcodeSource.model_validate(source.model_dump())
        if source.name.startswith("custom:"):
            raise ValueError("custom: is reserved for user-authored shortcodes")
        key = (
            str(UUID(identity))
            if identity
            else str(
                uuid5(NAMESPACE_URL, f"everunfold:plugin:{self._owner.id}:shortcode:{source.name}")
            )
        )
        compiled = compile_shortcode(source, key, revision)
        self._items.append(
            ShortcodeItem(
                id=key,
                origin="backend",
                editable=False,
                revision=revision,
                applied_revision=revision,
                archived=False,
                source=source,
                compiled=compiled,
            )
        )


class CaptchaRegistry:
    def __init__(self, owner: "PluginRegistry") -> None:
        self._owner = owner
        self._items: list[CaptchaProvider] = []

    def register(self, provider: CaptchaProvider) -> None:
        self._owner.check_open()
        if (
            not isinstance(provider, CaptchaProvider)
            or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", provider.id)
            or provider.id == "local"
            or not provider.title.strip()
            or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.:-]{0,127}", provider.widget)
            or not inspect.iscoroutinefunction(provider.verify)
        ):
            raise ValueError("Invalid CAPTCHA provider")
        self._items.append(provider)


class LocaleRegistry:
    def __init__(self, owner: "PluginRegistry") -> None:
        self._owner = owner
        self._items: dict[str, dict[str, str]] = {}

    def register(self, locale: str, messages: Mapping[str, str]) -> None:
        self._owner.check_open()
        if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", locale):
            raise ValueError("Invalid plugin locale")
        existing = self._items.setdefault(locale, {})
        for key, value in messages.items():
            if (
                not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.-]{0,127}", key)
                or not isinstance(value, str)
                or key in existing
            ):
                raise ValueError("Invalid or duplicate plugin translation")
            existing[key] = value


class TaskRegistry:
    def __init__(self, owner: "PluginRegistry") -> None:
        from app.core.jobs.definitions import TaskDefinition

        self.owner = owner
        self.items: list[TaskDefinition] = []

    def register(self, definition: "TaskDefinition") -> None:
        self.owner.check_open()
        if not definition.id.startswith(self.owner.id + ".") or any(
            x.id == definition.id for x in self.items
        ):
            raise ValueError("Task ID must be unique within its plugin namespace")
        self.items.append(definition)


class PluginRegistry:
    def __init__(self, identity: str) -> None:
        self.id = identity
        self._open = True
        self.shortcodes = ShortcodeRegistry(self)
        self.captcha = CaptchaRegistry(self)
        self.locales = LocaleRegistry(self)
        self.tasks = TaskRegistry(self)
        self.routes = RouteRegistry(self)

    def check_open(self) -> None:
        if not self._open:
            raise RuntimeError(f"Plugin setup has finished: {self.id}")

    def close(self) -> None:
        self._open = False
