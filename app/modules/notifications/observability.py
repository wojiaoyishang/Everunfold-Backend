"""Only stable delivery metadata crosses the business hook, never message data."""

from uuid import UUID

from app.core.plugins.business_events import BusinessEvent, BusinessResult

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


def delivery_event(
    status: str,
    channel: str,
    identity: UUID,
    *,
    kind: str = "",
    reason: str | None = None,
    attempt: int | None = None,
    stage: str = "delivery",
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
    )
