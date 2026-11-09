from typing import Any


def router_init(ctx: object) -> dict[str, Any]:
    from app.modules.captcha import comments as comments
    from app.modules.captcha import page_access as page_access
    from app.modules.captcha.providers import provider_catalog
    from app.modules.captcha.router import router

    provider_catalog()
    return {"id": "captcha", "version": "0.1.0", "router": router}
