"""Page configuration is JSON data; frontend controls do not define its storage schema."""

import json

from pydantic import JsonValue


def bounded_options(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    pending: list[tuple[JsonValue, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise ValueError("page configuration exceeds 32 nesting levels")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    if (
        len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())
        > 256 * 1024
    ):
        raise ValueError("page configuration exceeds 256 KiB")
    return value
