"""Native shortcode module, assembled exclusively by the API process."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from app.modules.shortcodes.router import router

    return {"id": "shortcodes", "version": "0.1.0", "router": router}
