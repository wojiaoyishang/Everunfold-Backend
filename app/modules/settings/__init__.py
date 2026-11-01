"""First-party settings module, assembled only by the API process."""

from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from app.modules.settings.router import router

    return {"id": "settings", "version": "0.1.0", "router": router}
