"""Static first-party module preflight and single-entrypoint FastAPI assembly."""

import importlib
import tomllib
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI


def register_content_module(application: FastAPI) -> None:
    path = Path(__file__).resolve().parents[1] / "modules" / "content" / "manifest.toml"
    with path.open("rb") as handle:
        manifest = tomllib.load(handle)
    if (
        manifest.get("id") != "content"
        or manifest.get("version") != "0.1.0"
        or manifest.get("dependencies") != []
    ):
        raise RuntimeError("Unsupported content module manifest")
    module_name, _, entrypoint = str(manifest["router_entrypoint"]).partition(":")
    module = importlib.import_module(module_name)
    initialize = getattr(module, entrypoint)
    result = cast(dict[str, Any], initialize(manifest))
    if result.get("id") != manifest["id"] or not isinstance(result.get("router"), APIRouter):
        raise RuntimeError("Invalid content module assembly result")
    application.include_router(result["router"])
