"""Private, resumable task progress. No browser callback URLs or secret task payloads."""

from fastapi import APIRouter

from app.core.jobs.progress import watch_progress
from app.core.jobs.progress_schemas import TaskProgress, WatchTask
from app.core.responses import JSONResponseSuccess
from app.core.schemas import ErrorResponse, PublicResponse
from app.modules.settings.router import Authorized

router = APIRouter(
    prefix="/v1/admin/tasks",
    tags=["settings"],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 404, 422, 500, 503)},
)


@router.post("/watch", response_model=PublicResponse[TaskProgress])
async def watch_task_progress(payload: WatchTask, ctx: Authorized) -> JSONResponseSuccess:
    # Commit only authentication last-used accounting, releasing its transaction before waiting.
    await ctx.service.session.commit()
    return JSONResponseSuccess(data=await watch_progress(ctx.service.config, ctx.actor, payload))
