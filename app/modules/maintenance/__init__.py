"""Core maintenance module registration."""

from typing import Any


def router_init(manifest: dict[str, Any]) -> dict[str, Any]:
    from .router import router

    return {"id": manifest["id"], "router": router}
