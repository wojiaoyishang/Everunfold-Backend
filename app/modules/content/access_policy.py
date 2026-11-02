"""Time is enforced by the server. Restricted bodies never enter shared projections."""

from datetime import UTC, datetime

from app.modules.content.models import Content


def publicly_readable(item: Content, now: datetime | None = None) -> bool:
    return item.visibility == "public" or bool(
        item.visibility == "password"
        and item.access_password_hash
        and item.opens_at
        and item.opens_at <= (now or datetime.now(UTC))
    )
