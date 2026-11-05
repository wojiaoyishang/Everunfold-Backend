"""Shared locale identifiers; interface preferences never rewrite content identity."""

from typing import Literal

Locale = Literal["zh-CN", "en"]
DEFAULT_LOCALE: Locale = "zh-CN"
LOCALES: tuple[Locale, ...] = ("zh-CN", "en")


def supported_locale(value: str | None) -> Locale | None:
    for entry in (value or "").split(","):
        code = entry.split(";", 1)[0].strip().replace("_", "-").lower()
        if code == "en" or code.startswith("en-"):
            return "en"
        if code in {"zh", "zh-cn", "zh-hans", "zh-hans-cn"}:
            return "zh-CN"
    return None


def resolve_locale(value: str | None) -> Locale:
    return supported_locale(value) or DEFAULT_LOCALE
