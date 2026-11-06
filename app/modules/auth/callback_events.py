"""Authorization changes and their delivery intent stay in the same PostgreSQL transaction."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.credentials_models import ApiApplication, ApiKey, AuthCallbackEvent
from app.modules.auth.models import User


def enqueue_callback(
    session: AsyncSession,
    app: ApiApplication,
    event_type: str,
    *,
    user: User | None = None,
    key: ApiKey | None = None,
    increment: bool = True,
) -> AuthCallbackEvent:
    if increment:
        app.auth_version += 1
    now, event_id = datetime.now(UTC), uuid4()
    payload: dict[str, object] = {
        "protocol": "everunfold.api-key.v1",
        "event_id": str(event_id),
        "app_id": app.id,
        "audience": app.audience,
        "type": event_type,
        "app_version": app.auth_version,
        "occurred_at": now.isoformat(),
    }
    if user:
        payload.update(user_id=str(user.id), user_version=user.auth_version)
    if key:
        payload.update(key_id=str(key.id), key_version=key.revision)
    event = AuthCallbackEvent(
        id=event_id,
        app_id=app.id,
        event_type=event_type,
        payload=payload,
        created_at=now,
        status="pending" if app.callback_url else "skipped",
        attempts=0,
        available_at=now,
    )
    session.add(event)
    return event


async def invalidate_user_keys(session: AsyncSession, user: User) -> None:
    app_ids = (
        select(ApiKey.app_id)
        .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
        .distinct()
    )
    applications = await session.scalars(
        select(ApiApplication)
        .where(ApiApplication.id.in_(app_ids))
        .order_by(ApiApplication.id)
        .with_for_update()
    )
    for app in applications:
        enqueue_callback(session, app, "user.authorization_changed", user=user)
