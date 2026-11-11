"""Immutable program dictionaries and isolated, explicitly overridable locales."""

import json
import logging
import re
import tomllib
from contextvars import ContextVar
from pathlib import Path
from types import MappingProxyType
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.locales import DEFAULT_LOCALE, LOCALES, Locale, resolve_locale, supported_locale

logger = logging.getLogger(__name__)
current_locale: ContextVar[Locale] = ContextVar("site_locale", default=DEFAULT_LOCALE)


class I18n:
    def __init__(self) -> None:
        self._catalogs: dict[str, Any] = {}
        self._source_keys: dict[str, str] = {}

    def load(self) -> None:
        if self._catalogs:
            return
        root = Path(__file__).resolve().parents[1]
        owners = [("core", root / "locales")]
        for manifest in sorted((root / "modules").glob("*/manifest.toml")):
            meta = tomllib.loads(manifest.read_text())
            owners.append((meta["id"], manifest.parent / "locales"))
        catalogs: dict[str, dict[str, str]] = {locale: {} for locale in LOCALES}
        for namespace, folder in owners:
            for locale in LOCALES:
                path = folder / f"{locale}.json"
                if not path.exists():
                    continue
                for key, value in json.loads(path.read_text()).items():
                    full = f"{namespace}.{key}"
                    if full in catalogs[locale] or not isinstance(value, str):
                        raise RuntimeError("Invalid or duplicate translation key")
                    catalogs[locale][full] = value
        from app.core.plugins.loader import plugin_catalog

        for plugin in plugin_catalog().plugins:
            for locale in LOCALES:
                for key, value in plugin.locales.get(locale, {}).items():
                    full = f"{plugin.info.id}.{key}"
                    if full in catalogs[locale]:
                        raise RuntimeError("Duplicate plugin translation key")
                    catalogs[locale][full] = value
        self._source_keys = {value: key for key, value in catalogs[DEFAULT_LOCALE].items()}
        self._catalogs = {key: MappingProxyType(value) for key, value in catalogs.items()}

    def t(self, key: str, *, locale: str | None = None, **params: object) -> str:
        self.load()
        selected = resolve_locale(locale) if locale else current_locale.get()
        value = self._catalogs.get(selected, {}).get(key)
        if value is None:
            logger.warning("missing_translation key=%s locale=%s", key, selected)
            value = self._catalogs.get(DEFAULT_LOCALE, {}).get(key, key)
        if not params:
            return str(value)
        fields = re.findall(r"(?<!\{)\{([^{}]+)\}(?!\})", value)
        if any(not field.isidentifier() or field not in params for field in fields) or any(
            not isinstance(value, (str, int, float, bool, type(None))) for value in params.values()
        ):
            logger.warning("invalid_translation_parameters key=%s", key)
            return key
        return str(value).format_map(params)

    def message(self, source: str) -> str:
        """Legacy static messages map to registered keys, never database/user text."""
        self.load()
        key = self._source_keys.get(source)
        return self.t(key) if key else source

    def catalog(self) -> dict[str, dict[str, str]]:
        """Return a copy of registered program messages for administrative overrides."""
        self.load()
        return {
            key: {
                locale: messages[key]
                for locale, messages in self._catalogs.items()
                if key in messages
            }
            for key in self._catalogs.get(DEFAULT_LOCALE, {})
        }


i18n = I18n()


class LocaleMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        locale = supported_locale(headers.get("X-Site-Locale")) or resolve_locale(
            headers.get("Accept-Language")
        )
        token = current_locale.set(locale)
        scope.setdefault("state", {})["locale"] = locale

        async def localized_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response = MutableHeaders(scope=message)
                response["Content-Language"] = locale
                response.append("Vary", "X-Site-Locale, Accept-Language")
            await send(message)

        try:
            await self.app(scope, receive, localized_send)
        finally:
            current_locale.reset(token)
