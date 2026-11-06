"""Validate only the bounded declarative controls registered by trusted frontend code."""

from copy import deepcopy
from urllib.parse import urlsplit

from pydantic import JsonValue

from app.modules.pages.schemas import PageOption


def safe_page_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    if (
        any(ord(c) < 32 for c in value)
        or "\\" in value
        or value.startswith("//")
        or (not value.startswith("/") and parsed.scheme not in {"http", "https"})
        or parsed.username
        or parsed.password
        or (parsed.scheme in {"http", "https"} and not parsed.hostname)
    ):
        raise ValueError("invalid page URL")
    return value


def validate_options(fields: list[PageOption], value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if set(value) - {f.key for f in fields}:
        raise ValueError("unknown page option")
    result: dict[str, JsonValue] = {}
    for field in fields:
        item = value.get(field.key, deepcopy(field.default))
        if field.kind == "boolean":
            if not isinstance(item, bool):
                raise ValueError("expected boolean")
        elif field.kind == "list":
            if not isinstance(item, list) or len(item) > field.max_items:
                raise ValueError("invalid option list")
            checked: list[JsonValue] = []
            for row in item:
                if not isinstance(row, dict):
                    raise ValueError("expected list object")
                checked.append(validate_options(field.fields, row))
            item = checked
        else:
            if not isinstance(item, str) or not field.min_length <= len(item) <= field.max_length:
                raise ValueError("invalid option text")
            if field.kind == "url":
                safe_page_url(item)
            if field.kind == "select" and item not in field.choices:
                raise ValueError("invalid option choice")
        result[field.key] = item
    return result
