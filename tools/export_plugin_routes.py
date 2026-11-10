"""Export one plugin's native OpenAPI without connecting to databases or activating it."""

import argparse
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.core.plugins.loader import PluginCatalog, plugin_catalog
from app.core.plugins.route_runtime import mount_plugin_routes


def build_schema(identity: str) -> dict[str, Any]:
    plugin = next((item for item in plugin_catalog().plugins if item.info.id == identity), None)
    if plugin is None or not plugin.routes:
        raise ValueError("Plugin is missing or contributes no HTTP routes")
    application = FastAPI(title=plugin.info.title, version=plugin.info.version)
    mount_plugin_routes(application, PluginCatalog((plugin,)))
    schema = application.openapi()
    schema["x-everunfold-plugin"] = identity
    schema["x-everunfold-plugin-build"] = plugin.info.build
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        schema = build_schema(args.plugin_id)
    except ValueError as error:
        parser.error(str(error))
    target = args.output or Path("var/cache/plugin-contracts") / (args.plugin_id + ".openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix(target.suffix + ".tmp")
    pending.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(target)
    print(f"Exported {args.plugin_id}: {len(schema['paths'])} paths")


if __name__ == "__main__":
    main()
