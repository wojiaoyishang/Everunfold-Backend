"""Worker adapter for notification event dispatch and platform delivery."""

from typing import Any

from app.core.config import Settings
from app.core.jobs.progress import task_progress


async def deliver(config: Settings) -> dict[str, Any]:
    from app.modules.notifications.delivery import deliver_notifications

    await task_progress(stage="notifications", message="Processing notification deliveries")
    result = await deliver_notifications(config)
    await task_progress(
        stage="notifications",
        message="Finished notification deliveries",
        completed=result["delivered"],
        total=result["dispatched"] + result["delivered"],
        unit="items",
    )
    return result
