"""Restricted notification interpolation: no template code is executed."""

import html
import re
from collections.abc import Mapping
from typing import Any

TOKEN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")


def value_at(data: Mapping[str, Any], path: str) -> str:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return ""
        value = value[part]
    return "" if value is None else str(value)


def render_template(template: str, data: Mapping[str, Any]) -> str:
    return TOKEN.sub(lambda match: value_at(data, match.group(1)), template)


def header_value(value: str, limit: int = 300) -> str:
    """Prevent event data from injecting additional mail headers."""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())[:limit]


def text_html(value: str) -> str:
    return "<pre>" + html.escape(value) + "</pre>"
