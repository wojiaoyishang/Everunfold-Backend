"""Public parameter descriptions never publish secret defaults or lossy Secret models."""

from copy import deepcopy
from dataclasses import is_dataclass
from typing import Any, get_args, get_type_hints, is_typeddict

from pydantic import BaseModel, SecretBytes, SecretStr


def reject_secret_models(model: type[BaseModel]) -> None:
    seen: set[int] = set()

    def walk(annotation: Any) -> None:
        if id(annotation) in seen:
            return
        seen.add(id(annotation))
        if isinstance(annotation, type):
            if issubclass(annotation, (SecretStr, SecretBytes)):
                raise ValueError(
                    "Task parameters cannot use SecretStr/SecretBytes; use JSON fields with "
                    "writeOnly metadata or a server-side credential reference"
                )
            if issubclass(annotation, BaseModel):
                annotation.model_rebuild()
                for field in annotation.model_fields.values():
                    walk(field.annotation)
            elif is_dataclass(annotation) or is_typeddict(annotation):
                for field_type in get_type_hints(annotation, include_extras=True).values():
                    walk(field_type)
        for argument in get_args(annotation):
            walk(argument)

    walk(model)


_OMIT = object()


def _description(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    while isinstance(node.get("$ref"), str):
        reference = node["$ref"]
        if reference in seen or not reference.startswith("#/"):
            break
        seen.add(reference)
        resolved: Any = root
        for key in reference[2:].split("/"):
            resolved = resolved.get(key.replace("~1", "/").replace("~0", "~"), {})
        if not isinstance(resolved, dict):
            break
        node = {**resolved, **{key: value for key, value in node.items() if key != "$ref"}}
    return node


def _redact(value: Any, node: dict[str, Any], root: dict[str, Any]) -> Any:
    node = _description(node, root)
    if node.get("writeOnly") is True or node.get("format") == "password":
        return _OMIT
    for keyword in ("anyOf", "oneOf", "allOf"):
        for variant in node.get(keyword, []):
            if isinstance(variant, dict):
                value = _redact(value, variant, root)
                if value is _OMIT:
                    return _OMIT
    if isinstance(value, dict):
        properties = node.get("properties", {})
        extra = node.get("additionalProperties", {})
        return {
            key: public
            for key, item in value.items()
            if (
                public := _redact(
                    item,
                    properties.get(key, extra if isinstance(extra, dict) else {}),
                    root,
                )
            )
            is not _OMIT
        }
    if isinstance(value, list):
        schema = node.get("items", {})
        return [
            public
            for item in value
            if (public := _redact(item, schema if isinstance(schema, dict) else {}, root))
            is not _OMIT
        ]
    return value


def public_parameters(
    schema: dict[str, Any], defaults: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    public = deepcopy(schema)

    def clean(node: Any) -> None:
        if isinstance(node, dict):
            sensitive = _redact(None, node, schema) is _OMIT
            for key in ("default", "examples", "example"):
                if key not in node:
                    continue
                if sensitive:
                    node.pop(key)
                elif key == "examples" and isinstance(node[key], list):
                    node[key] = [
                        value
                        for item in node[key]
                        if (value := _redact(item, node, schema)) is not _OMIT
                    ]
                else:
                    value = _redact(node[key], node, schema)
                    if value is _OMIT:
                        node.pop(key)
                    else:
                        node[key] = value
            for keyword in ("properties", "$defs", "definitions", "patternProperties"):
                for child in node.get(keyword, {}).values():
                    clean(child)
            for keyword in (
                "items",
                "additionalProperties",
                "contains",
                "not",
                "if",
                "then",
                "else",
                "propertyNames",
            ):
                child = node.get(keyword)
                if isinstance(child, dict):
                    clean(child)
            for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
                for child in node.get(keyword, []):
                    clean(child)

    clean(public)
    result = _redact(defaults, schema, schema)
    return public, result if isinstance(result, dict) else {}
