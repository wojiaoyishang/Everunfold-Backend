"""Static first-party module preflight and single-entrypoint FastAPI assembly."""

import importlib
import tomllib
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI


def register_content_module(application: FastAPI) -> None:
    _register_module(application, "content")


def register_captcha_module(application: FastAPI) -> None:
    _register_module(application, "captcha")


def register_auth_module(application: FastAPI) -> None:
    _register_module(application, "auth")


def register_media_module(application: FastAPI) -> None:
    _register_module(application, "media")


def register_settings_module(application: FastAPI) -> None:
    _register_module(application, "settings")


def register_pages_module(application: FastAPI) -> None:
    _register_module(application, "pages")


def register_shortcodes_module(application: FastAPI) -> None:
    _register_module(application, "shortcodes")


def register_notifications_module(application: FastAPI) -> None:
    _register_module(application, "notifications")


def register_maintenance_module(application: FastAPI) -> None:
    _register_module(application, "maintenance")


def _register_module(application: FastAPI, module_id: str) -> None:
    path = Path(__file__).resolve().parents[1] / "modules" / module_id / "manifest.toml"
    with path.open("rb") as handle:
        manifest = tomllib.load(handle)
    if (
        manifest.get("id") != module_id
        or manifest.get("version") != "0.1.0"
        or manifest.get("dependencies") != []
    ):
        raise RuntimeError("Unsupported first-party module manifest")
    module_name, _, entrypoint = str(manifest["router_entrypoint"]).partition(":")
    module = importlib.import_module(module_name)
    initialize = getattr(module, entrypoint)
    result = cast(dict[str, Any], initialize(manifest))
    if result.get("id") != manifest["id"] or not isinstance(result.get("router"), APIRouter):
        raise RuntimeError("Invalid first-party module assembly result")
    application.include_router(result["router"])
