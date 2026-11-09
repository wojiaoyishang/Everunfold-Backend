"""Worker adapters around existing transactional services."""

from typing import Any

from app.core.config import Settings
from app.core.jobs.progress import task_progress


async def settings(config: Settings) -> dict[str, Any]:
    from app.modules.settings.delivery import deliver_batch

    await task_progress(stage="settings", message="Processing settings delivery")
    completed = await deliver_batch(config)
    await task_progress(
        stage="settings",
        message="Finished settings delivery",
        completed=completed,
        total=completed,
        unit="items",
    )
    return {"completed": completed}


async def callbacks(config: Settings) -> dict[str, Any]:
    from app.modules.auth.callback_delivery import deliver_callbacks

    await task_progress(stage="callbacks", message="Processing application callbacks")
    completed = await deliver_callbacks(config)
    await task_progress(
        stage="callbacks",
        message="Finished application callbacks",
        completed=completed,
        total=completed,
        unit="items",
    )
    return {"completed": completed}


async def publication(config: Settings) -> dict[str, Any]:
    from app.modules.content.scheduled_publication import publish_due

    await task_progress(stage="publication", message="Processing scheduled publication")
    completed = await publish_due(config)
    await task_progress(
        stage="publication",
        message="Finished scheduled publication",
        completed=completed,
        total=completed,
        unit="items",
    )
    return {"completed": completed}
