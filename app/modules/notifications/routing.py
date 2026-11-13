"""Resolve the mail sender for purpose-bound authentication deliveries."""

from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.catalog import BUILTIN_BY_ID
from app.modules.notifications.models import NotificationRoute
from app.modules.settings.mail_service import MailProfile, mail_profile

IN_APP = "in-app"
EMAIL = "email"


def route_id(event: str, kind: str, value: str, platform: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"everunfold:notification-route:{event}:{kind}:{value}:{platform}")


@dataclass(frozen=True)
class AuthMailRoute:
    enabled: bool = True
    profile_id: str | None = None


async def auth_mail_route(session: AsyncSession, event_type: str) -> AuthMailRoute:
    definition = BUILTIN_BY_ID[event_type]
    if definition.delivery_mode == "notification":
        raise ValueError("Not a purpose-bound authentication mail event")
    route = await session.scalar(
        select(NotificationRoute).where(
            NotificationRoute.event_type == event_type,
            NotificationRoute.recipient_type == "subject",
            NotificationRoute.recipient_value == "subject",
            NotificationRoute.platform_id == EMAIL,
        )
    )
    if route is None:
        return AuthMailRoute()
    return AuthMailRoute(
        enabled=definition.requires_code or route.enabled,
        profile_id=route.mail_profile_id,
    )


async def auth_code_mail(
    session: AsyncSession, data: dict[str, Any], event_type: str, fallback: str | None
) -> MailProfile | None:
    route = await auth_mail_route(session, event_type)
    return mail_profile(data, route.profile_id if route.profile_id is not None else fallback)
