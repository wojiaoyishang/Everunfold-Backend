"""Claim short leases, deliver outside PostgreSQL, and fence late worker acknowledgments."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2 as httpx
from sqlalchemy import or_, select

from app.core.config import Settings
from app.core.database import Database
from app.modules.auth.application_service import callback_origin
from app.modules.auth.application_signatures import (
    application_secret,
    key_id,
    signed_headers,
    verify_signature,
)
from app.modules.auth.credentials_models import ApiApplication, AuthCallbackEvent
from app.modules.auth.credentials_schemas import CallbackReceipt
from app.modules.auth.service import AuthFailure


async def send_callback(
    config: Settings, app: ApiApplication, payload: dict[str, object]
) -> tuple[str, bool]:
    """An HTTP 200 is not an application acknowledgement; require a bound signed receipt."""
    if not app.callback_url:
        return "target_removed", False
    try:
        if callback_origin(app.callback_url) not in {
            callback_origin(value) for value in config.auth.callback_allowed_origins
        }:
            return "origin_not_allowed", False
        secret = application_secret(config.auth, app.id, app.callback_key_version, "callback")
    except AuthFailure:
        return "signing_or_target_unavailable", False
    kid = key_id(app, "callback")
    if kid is None:
        return "signing_unavailable", False
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    headers = signed_headers(secret, app.id, kid, "POST", app.callback_url, body)
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(
                    config.outbox.request_timeout_seconds,
                    connect=config.outbox.connect_timeout_seconds,
                ),
            ) as client,
            client.stream("POST", app.callback_url, content=body, headers=headers) as response,
        ):
            if response.status_code != 200:
                return f"http_{response.status_code}", False
            parts: list[bytes] = []
            size = 0
            async for part in response.aiter_bytes():
                size += len(part)
                if size > 65536:
                    return "receipt_too_large", False
                parts.append(part)
            raw = b"".join(parts)
            try:
                nonce = verify_signature(
                    secret, app.id, kid, "RESPONSE", app.callback_url, raw, response.headers
                )
            except AuthFailure:
                return "invalid_signature", False
            if nonce != headers["X-Everunfold-Nonce"]:
                return "invalid_signature", False
            receipt = CallbackReceipt.model_validate_json(raw)
            if (
                str(receipt.event_id) != payload["event_id"]
                or receipt.app_id != app.id
                or receipt.app_version < int(str(payload["app_version"]))
            ):
                return "invalid_receipt", False
            return ("" if receipt.applied else "not_applied"), True
    except (httpx.TransportError, ValueError, KeyError):
        return "delivery_unavailable", False


async def deliver_callbacks(config: Settings) -> int:
    database = Database(config.database.url.get_secret_value())
    completed = 0
    try:
        # Bound one tick's network work so the shared worker also services publication events.
        for _ in range(min(config.outbox.batch_size, 4)):
            now, lease = datetime.now(UTC), uuid4()
            async with database.session_factory() as session:
                row = await session.scalar(
                    select(AuthCallbackEvent)
                    .where(
                        AuthCallbackEvent.status == "pending",
                        AuthCallbackEvent.available_at <= now,
                        or_(
                            AuthCallbackEvent.lease_until.is_(None),
                            AuthCallbackEvent.lease_until < now,
                        ),
                    )
                    .order_by(AuthCallbackEvent.available_at, AuthCallbackEvent.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    break
                app = await session.get(ApiApplication, row.app_id)
                if app is None or not app.callback_url:
                    row.status, row.last_error = "skipped", "target_removed"
                    await session.commit()
                    continue
                if row.attempts >= config.outbox.max_attempts:
                    row.status = "failed"
                    await session.commit()
                    continue
                row.attempts += 1
                row.lease_id = lease
                row.lease_until = now + timedelta(
                    seconds=max(
                        config.outbox.lease_seconds, config.outbox.request_timeout_seconds + 10
                    )
                )
                event_id, payload = row.id, row.payload
                # expire_on_commit=False retains a plain snapshot; no connection spans HTTP.
                await session.commit()
            error, received = await send_callback(config, app, payload)
            async with database.session_factory() as session:
                row = await session.get(AuthCallbackEvent, event_id, with_for_update=True)
                if row is None or row.lease_id != lease:
                    continue
                row.lease_id = row.lease_until = None
                row.last_error = error
                now = datetime.now(UTC)
                if received:
                    row.received_at = row.received_at or now
                if not error:
                    row.status, row.applied_at = "delivered", now
                    completed += 1
                elif error == "target_removed":
                    row.status = "skipped"
                elif row.attempts >= config.outbox.max_attempts:
                    row.status = "failed"
                else:
                    row.available_at = now + timedelta(
                        seconds=min(
                            2 ** min(row.attempts, 16), config.outbox.retry_max_delay_seconds
                        )
                    )
                await session.commit()
        return completed
    finally:
        await database.close()
