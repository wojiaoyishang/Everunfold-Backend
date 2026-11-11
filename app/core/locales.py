"""Shared locale identifiers; interface preferences never rewrite content identity."""

import re
from typing import Literal

Locale = Literal["zh-CN", "en"]
DEFAULT_LOCALE: Locale = "zh-CN"
LOCALES: tuple[Locale, ...] = ("zh-CN", "en")


def supported_locale(value: str | None) -> Locale | None:
    choices: list[tuple[float, int, str]] = []
    for order, entry in enumerate((value or "").split(",")):
        parts = entry.split(";")
        quality = 1.0
        try:
            for parameter in parts[1:]:
                if parameter.strip().startswith("q="):
                    quality = float(parameter.strip()[2:])
        except ValueError:
            continue
        if 0 < quality <= 1:
            choices.append((-quality, order, parts[0]))
    for _, _, entry in sorted(choices):
        code = entry.split(";", 1)[0].strip().replace("_", "-").lower()
        if code == "en" or code.startswith("en-"):
            return "en"
        if code in {"zh", "zh-cn", "zh-hans", "zh-hans-cn"}:
            return "zh-CN"
    return None


def resolve_locale(value: str | None) -> Locale:
    return supported_locale(value) or DEFAULT_LOCALE


def request_locale(value: str | None) -> str | None:
    """Accept a BCP47 request locale while keeping built-in packages separate."""
    candidate = (value or "").strip()
    return candidate if re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", candidate) else None
