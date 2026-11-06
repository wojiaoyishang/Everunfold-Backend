"""Explicit first-party page management assembly."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from app.modules.pages.router import router

    return {"id": "pages", "version": "0.1.0", "router": router}
