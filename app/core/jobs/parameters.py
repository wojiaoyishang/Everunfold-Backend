"""Bounded JSON arguments, validated independently by API and execution process."""

import json
import math
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.core.jobs.definitions import TaskDefinition


def checked_json(value: dict[str, Any]) -> str:
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 16:
            raise ValueError("Task parameters exceed maximum nesting depth")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Task parameter keys must be strings")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError("Task parameters must contain JSON values")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Task parameters must contain finite JSON numbers")
    encoded = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    if len(encoded.encode()) > 32768:
        raise ValueError("Task parameters exceed 32 KiB")
    return encoded


def validate_parameters(spec: "TaskDefinition", value: dict[str, Any]) -> BaseModel | None:
    encoded = checked_json(value)
    if spec.parameters is None:
        if value:
            raise ValueError("This task does not accept parameters")
        return None
    return spec.parameters.model_validate_json(encoded, strict=True, extra="forbid")


def normalized_parameters(spec: "TaskDefinition", value: dict[str, Any]) -> dict[str, Any]:
    model = validate_parameters(spec, value)
    normalized = (
        model.model_dump(mode="json", by_alias=True, exclude_computed_fields=True)
        if model is not None
        else {}
    )
    if not isinstance(normalized, dict):
        raise ValueError("Task parameters must serialize to a JSON object")
    # Also validate model defaults/serializers, not merely the explicitly supplied values.
    validate_parameters(spec, normalized)
    checked_json(normalized)
    return normalized


def parameters_schema(spec: "TaskDefinition") -> dict[str, Any]:
    if spec.parameters is None:
        return {}
    schema = spec.parameters.model_json_schema()

    def forbid_unknown(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" and "properties" in value:
                value.setdefault("additionalProperties", False)
            for child in value.values():
                forbid_unknown(child)
        elif isinstance(value, list):
            for child in value:
                forbid_unknown(child)

    forbid_unknown(schema)
    return schema
