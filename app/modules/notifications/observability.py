"""Only stable delivery metadata crosses the business hook, never message data."""

from uuid import UUID

from app.core.plugins.business_events import BusinessEvent, BusinessResult
from app.core.plugins.business_identity import email_domain

RESULTS: dict[str, BusinessResult] = {
    "sent": "success",
    "failed": "failure",
    "suppressed": "suppressed",
    "unknown": "unknown",
}
REASONS = {
    "authentication",
    "connection",
    "timeout",
    "tls",
    "unsupported",
    "transport",
    "sender_rejected",
    "recipient_rejected",
    "message_rejected",
    "delivery_unknown",
    "delivery_failed",
    "execution_lease_expired",
    "event_unavailable",
    "recipient_changed",
    "recipient_preference_changed",
    "reply_unavailable",
    "mail_configuration_changed",
    "email_not_configured",
    "missing_recipient",
    "unverified_recipient",
    "platform_unavailable",
    "platform_not_synced",
    "platform_exception",
    "lease_lost",
    "already_verified",
    "recipient_unavailable",
    "request_unavailable",
}


def mail_profile_identifier(value: str | UUID | None) -> str:
    if value is None:
        return ""
    try:
        return str(UUID(str(value)))
    except ValueError:
        return ""


def delivery_event(
    status: str,
    channel: str,
    identity: UUID,
    *,
    kind: str = "",
    reason: str | None = None,
    attempt: int | None = None,
    stage: str = "delivery",
    recipient: str = "",
    mail_profile_id: str | UUID | None = None,
    duration_ms: float | None = None,
) -> BusinessEvent:
    result = RESULTS[status]
    if reason not in REASONS:
        reason = (
            {
                "failure": "delivery_failed",
                "unknown": "delivery_unknown",
                "suppressed": "request_unavailable",
            }.get(result)
            if reason
            else None
        )
    return BusinessEvent(
        "notification",
        result,
        stage=stage,
        resource_id=identity,
        channel=channel,
        notification_type=kind,
        reason=reason or "",
        attempt=attempt,
        recipient_domain=email_domain(recipient) if channel == "email" else "",
        mail_profile_id=mail_profile_identifier(mail_profile_id) if channel == "email" else "",
        delivery_duration_ms=duration_ms,
    )
