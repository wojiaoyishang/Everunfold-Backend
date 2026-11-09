"""Worker adapters around existing transactional services."""

from typing import Any

from app.core.config import Settings


async def settings(config: Settings) -> dict[str, Any]:
    from app.modules.settings.delivery import deliver_batch

    return {"completed": await deliver_batch(config)}


async def callbacks(config: Settings) -> dict[str, Any]:
    from app.modules.auth.callback_delivery import deliver_callbacks

    return {"completed": await deliver_callbacks(config)}


async def publication(config: Settings) -> dict[str, Any]:
    from app.modules.content.scheduled_publication import publish_due

    return {"completed": await publish_due(config)}
