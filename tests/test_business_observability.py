"""Business hooks respect deployment state and report real outcomes without private data."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import update

from app.core.database import Database
from app.core.plugins.business_events import BusinessEvent, dispatch, subscribers
from app.core.plugins.loader import collect_plugins, plugin_catalog
from app.core.plugins.state import CATALOG_KEY
from app.core.process_logging import BUSINESS_LOGGER, RuntimeLogFormatter
from app.modules.auth.email_verification_delivery import deliver as deliver_verification
from app.modules.auth.email_verification_models import EmailVerification
from app.modules.auth.models import User
from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import SiteSettings
from app.modules.content.scheduled_publication import publish_due
from app.modules.notifications.catalog import EMAIL_VERIFICATION
from app.modules.notifications.delivery import deliver_notifications
from app.modules.notifications.events import publish_event
from app.modules.notifications.models import NotificationDelivery, NotificationRoute
from app.modules.notifications.routing import EMAIL, IN_APP
from app.modules.settings.backend_plugins import install_backend_plugins
from tests.test_accounts import AccountsTest, headers, signed_in
from tests.test_mail_delivery import preferences, smtp_server
from tests.test_page_comments import approve, comment, comments, custom

business_database = comments

PLUGIN_ID = "core.business-observability"


def test_business_hook_checks_activation_and_isolates_handler_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalog = plugin_catalog()
    applied = {CATALOG_KEY: catalog.snapshot()}
    handlers = subscribers(applied)
    assert PLUGIN_ID in [owner for owner, _ in handlers]
    assert not subscribers({**applied, "extensions": {"disabled_plugins": [PLUGIN_ID]}})
    assert not subscribers({})
    for item in applied[CATALOG_KEY]:
        if item["info"]["id"] == PLUGIN_ID:
            item["info"]["build"] = "f" * 64
    assert not subscribers(applied)

    def broken(event: BusinessEvent) -> None:
        raise RuntimeError("never write the exception detail")

    seen: list[BusinessEvent] = []
    event = BusinessEvent("login", "success")
    dispatch((("example.broken", broken), ("example.good", seen.append)), event)
    assert seen == [event]
    assert "example.broken failed (RuntimeError)" in caplog.text
    assert "never write the exception detail" not in caplog.text

    with pytest.raises(ValueError, match="synchronous"):
        from app.plugin_sdk import PluginRegistry, define_plugin

        async def invalid(event: BusinessEvent) -> None:
            return None

        def setup(registry: PluginRegistry) -> None:
            registry.business_events.register(invalid)  # type: ignore[arg-type]

        collect_plugins(
            [(define_plugin(id="test.invalid", title="Invalid", setup=setup), "f" * 64)]
        )


async def install(database: Database, ctx: AccountsTest) -> None:
    async with database.session_factory() as session:
        await install_backend_plugins(session, ctx.settings)
        site = await session.get(SiteSettings, 1, with_for_update=True)
        assert site is not None
        site.data = {**site.data, "extensions": {"enabled_plugins": [PLUGIN_ID]}}
        await session.commit()
        assert subscribers(site.data)


def business_rows(caplog: pytest.LogCaptureFixture, component: str) -> list[dict[str, object]]:
    formatter = RuntimeLogFormatter(component)
    formatter.text = False
    return [
        json.loads(formatter.format(record))
        for record in caplog.records
        if record.name == BUSINESS_LOGGER and hasattr(record, "business_event")
    ]


def test_real_http_business_events_are_committed_once(
    business_database: AccountsTest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = business_database
    database = Database(ctx.settings.database.url.get_secret_value())

    async def prepare() -> None:
        try:
            await install(database, ctx)
        finally:
            await database.close()

    asyncio.run(prepare())
    caplog.set_level(logging.INFO, logger=BUSINESS_LOGGER)
    caplog.set_level(logging.INFO, logger="uvicorn.error.everunfold.http")
    ctx.root = ctx.client()
    visitor = ctx.client()
    bad = visitor.post(
        "/v1/auth/login",
        json={
            "username": ctx.name,
            "password": "sentinel-login-password",
        },
        headers=headers(visitor),
    )
    assert bad.status_code == 401
    signed_in(ctx.root, ctx.name)
    target = custom(ctx)
    payload = comment(target["id"], body="sentinel-comment-body")
    submit = "/v1/reader/comments"
    assert visitor.post(submit, json={}, headers=headers(visitor)).status_code == 422
    posted = ctx.root.post(submit, json=payload, headers=headers(ctx.root))
    assert posted.status_code == 200, posted.text
    assert ctx.root.post(submit, json=payload, headers=headers(ctx.root)).status_code == 200
    approve(ctx, posted.json()["data"]["id"])

    def content_post(action, data):
        return ctx.root.post("/v1/admin/content/" + action, json=data, headers=headers(ctx.root))

    fields = {
        "title": "sentinel-draft-title",
        "body": [{"id": str(uuid4()), "type": "markdown", "content": "sentinel-draft-body"}],
        "settings": {"path": "/business-test.html"},
    }
    created = content_post(
        "create", {"type_id": "article", "operation_id": str(uuid4()), "fields": fields}
    )
    assert created.status_code == 200, created.text
    draft = created.json()["data"]
    command = {
        "id": draft["id"],
        "expected_revision": draft["revision"],
        "operation_id": str(uuid4()),
        "fields": fields,
    }
    published = content_post("publish", command)
    assert published.status_code == 200, published.text
    assert content_post("publish", command).status_code == 200
    later = content_post(
        "schedule",
        {
            **command,
            "operation_id": str(uuid4()),
            "expected_revision": published.json()["data"]["revision"],
            "scheduled_for": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
    )
    assert later.status_code == 200, later.text

    async def scheduled() -> None:
        try:
            async with database.session_factory() as session:
                await session.execute(
                    update(ContentDraft)
                    .where(ContentDraft.id == UUID(draft["id"]))
                    .values(scheduled_for=datetime.now(UTC) - timedelta(seconds=1))
                )
                await session.commit()
            assert await publish_due(ctx.settings) == 1
            assert await publish_due(ctx.settings) == 0
        finally:
            await database.close()

    asyncio.run(scheduled())
    rows = business_rows(caplog, "fastapi")
    symbols = [row["symbol"] for row in rows]
    assert symbols.count("business.login.success") == 1
    assert symbols.count("business.login.failure") == 1
    assert symbols.count("business.comment_submit.success") == 1
    assert symbols.count("business.comment_submit.failure") == 1
    assert symbols.count("business.comment_moderate.success") == 1
    assert symbols.count("business.page_visibility.success") == 1
    assert symbols.count("business.schedule.success") == 1
    publications = [row for row in rows if row["symbol"] == "business.publish.success"]
    assert [(row["business_stage"], row["business_state"]) for row in publications] == [
        ("manual", "first"),
        ("scheduled", "update"),
    ]
    denied = next(row for row in rows if row["symbol"] == "business.login.failure")
    assert (denied["status"], denied["business_error_code"]) == (401, 40110)
    assert any(
        getattr(record, "http_access", {}).get("request_id") == denied["request_id"]
        for record in caplog.records
    )
    serialized = json.dumps(rows)
    assert "sentinel-" not in serialized
    assert ctx.name not in serialized


def test_notifications_record_outcomes_from_smtp_and_inbox(
    business_database: AccountsTest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = business_database
    database = Database(ctx.settings.database.url.get_secret_value())
    mail_id = uuid4()
    recipient = "private-recipient@example.test"
    caplog.set_level(logging.INFO, logger=BUSINESS_LOGGER)

    async def queue(port: int, *, security_mail: bool = False) -> None:
        async with database.session_factory() as session:
            site = await session.get(SiteSettings, 1, with_for_update=True)
            assert site is not None
            site.data = {
                **site.data,
                "_mail_private": {
                    "revision": 1,
                    "profiles": [
                        {
                            "id": str(mail_id),
                            "name": "SMTP fixture",
                            "enabled": True,
                            "is_default": True,
                            "value": preferences(port, enabled=True).model_dump(mode="json"),
                            "password": "",
                        }
                    ],
                },
            }
            initial = ctx.root.get("/v1/auth/session").json()["data"]
            user_id = UUID(initial["user"]["id"])
            user = await session.get(User, user_id)
            assert user is not None
            now = datetime.now(UTC)
            if security_mail:
                session.add(
                    EmailVerification(
                        id=uuid4(),
                        user_id=user_id,
                        email=recipient,
                        salt="salt",
                        code_hash="hash",
                        delivery_code="918274",
                        mail_profile_id=str(mail_id),
                        mail_revision=1,
                        profile_revision=user.revision,
                        created_at=now,
                        expires_at=now + timedelta(minutes=10),
                    )
                )
                await session.commit()
                return
            user.email = recipient
            user.email_verified_at = now
            identity = uuid4()
            queued = await publish_event(
                session,
                event_type="comment.pending_review",
                dedupe_key=f"business:{identity}",
                resource_type="comment",
                resource_id=str(identity),
                actor_id=None,
                data={"actor": {"name": "Reader"}, "content": {"excerpt": "sentinel-body"}},
            )
            assert queued
            session.add(
                NotificationDelivery(
                    id=identity,
                    event_id=uuid5(NAMESPACE_URL, f"everunfold:notification:business:{identity}"),
                    user_id=user_id,
                    platform_id=EMAIL,
                    mail_profile_id=str(mail_id),
                    mail_config_revision=1,
                    locale="en",
                    subject="sentinel-subject",
                    text="sentinel-content",
                    destination=recipient,
                    next_attempt_at=now,
                    created_at=now,
                )
            )
            # The event is dispatched by the worker, including any enabled inbox routes.
            await session.commit()

    async def exercise() -> None:
        try:
            await install(database, ctx)
            async with database.session_factory() as session:
                session.add(
                    NotificationRoute(
                        id=uuid4(),
                        event_type="comment.pending_review",
                        platform_id=IN_APP,
                        recipient_type="role",
                        recipient_value="admin",
                        enabled=True,
                        updated_at=datetime.now(UTC),
                    )
                )
                await session.commit()
            with smtp_server() as (port, messages):
                await queue(port, security_mail=True)
                assert await deliver_verification(ctx.settings) == {"processed": 1}
                assert any(b"918274" in message for message in messages)
                await queue(port)
                await deliver_notifications(ctx.settings)
                assert len(messages) == 2
            with smtp_server(reject=True) as (port, _):
                await queue(port)
                await deliver_notifications(ctx.settings)
            with smtp_server(drop=True) as (port, _):
                await queue(port)
                await deliver_notifications(ctx.settings)
        finally:
            await database.close()

    asyncio.run(exercise())
    rows = business_rows(caplog, "celery")
    assert (
        sum(
            row.get("business_channel") == EMAIL and row["business_result"] == "success"
            for row in rows
        )
        == 2
    )
    assert any(row.get("business_notification_type") == EMAIL_VERIFICATION for row in rows)
    assert any(
        row.get("business_reason") == "recipient_rejected" and row["business_result"] == "failure"
        for row in rows
    )
    assert any(
        row.get("business_reason") == "delivery_unknown" and row["business_result"] == "unknown"
        for row in rows
    )
    assert any(
        row.get("business_channel") == IN_APP and row["business_result"] == "success"
        for row in rows
    )
    serialized = json.dumps(rows, ensure_ascii=False)
    assert not any(
        value in serialized
        for value in (
            recipient,
            "sentinel-",
            "918274",
            "sentinel-do-not-expose",
        )
    )
