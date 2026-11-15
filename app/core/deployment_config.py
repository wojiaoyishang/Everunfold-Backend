"""Read the private root deployment document without logging its values."""

import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml


class DeploymentConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader: UniqueLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise DeploymentConfigError("Root YAML contains duplicate or invalid keys")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def read_deployment(path: Path) -> dict[str, Any]:
    try:
        source = path.read_bytes()
        if len(source) > 1024 * 1024:
            raise ValueError
        document = yaml.load(source, Loader=UniqueLoader)
        json.dumps(document)
        if not isinstance(document, dict):
            raise ValueError
        if document.get("version") != 1 or document.get("component") != "backend":
            raise ValueError
        if not isinstance(document.get("application"), dict):
            raise ValueError
        environment = document.get("environment", {})
        if not isinstance(environment, dict) or not all(
            isinstance(key, str)
            and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key)
            and isinstance(value, str)
            and not any(ord(char) < 32 or ord(char) == 127 for char in value)
            for key, value in environment.items()
        ):
            raise ValueError
        return copy.deepcopy(document)
    except FileNotFoundError:
        raise DeploymentConfigError(f"Configuration file not found: {path}") from None
    except (OSError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise DeploymentConfigError("Root config.yaml is unreadable or invalid") from None
