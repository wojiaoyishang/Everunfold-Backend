"""Notification routing, inbox, template safety and retry boundaries."""

import asyncio
import json
import os
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import asyncpg  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.core.plugins.definition import define_plugin
from app.core.plugins.loader import collect_plugins
from app.entrypoints.api import create_app
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.models import User
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import default_template
from app.modules.notifications.delivery import deliver_notifications
from app.modules.notifications.email_context import comment_data, email_urls
from app.modules.notifications.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationInbox,
)
from app.modules.notifications.platforms import (
    NotificationDeliveryRequest,
    NotificationDeliveryResult,
    NotificationPlatform,
)
from app.modules.notifications.render import (
    header_value,
    render_html_template,
    render_template,
    sanitize_email_html,
)
from app.modules.notifications.service import NotificationService, publish_event
from app.modules.settings.delivery import deliver_batch
from app.modules.settings.models import SettingsAudit, SettingsDeliveryEvent
from tests.test_accounts import ORIGIN, PASSWORD, headers, signed_in
from tests.test_settings_processes import toml_text
from tools.export_notifications_contract import build_schema

ROOT = Path(__file__).resolve().parents[1]


def test_notification_contract_matches_published_source() -> None:
    assert (
        json.loads((ROOT / "contracts/notifications-v1.schema.json").read_text()) == build_schema()
    )


def test_templates_are_restricted_interpolation_and_missing_values_are_safe() -> None:
    value = "{{ actor.name }} / {{resource.title}} / {{missing.value}} / {{x}}"
    assert render_template(value, {"actor": {"name": "Ada"}}) == "Ada /  /  / "
    assert render_template("{{resource.url}}", {"resource": {"url": "javascript:alert(1)"}}) == (
        "javascript:alert(1)"
    )
    assert header_value("Title\r\nBcc: victim@example.test") == "Title Bcc: victim@example.test"
    assert default_template("comment.pending_review", "in-app", "fr").subject == "评论待审核"
    default_email = default_template("comment.pending_review", "email", "zh-CN")
    assert "<table" in default_email.html and "{{actor.name}}" in default_email.html
    assert 'href="{{resource.review_url}}"' in default_email.html
    for event_type in (
        "content.scheduled_published",
        "content.schedule_failed",
        "settings.delivery_failed",
    ):
        template = default_template(event_type, "email", "zh-CN")
        assert template.subject and template.text and "<table" in template.html
    assert (
        render_html_template(
            "<p>{{actor.name}}</p>",
            {"actor": {"name": "<strong>Reader</strong>"}},
        )
        == "<p>&lt;strong&gt;Reader&lt;/strong&gt;</p>"
    )
    sanitized = sanitize_email_html(
        '<p onclick="alert(1)" style="color:red">Hello'
        "<script>alert(1)</script>"
        '<a href="javascript:alert(1)">link</a>'
        '<img src="https://example.test/image.png" onerror="alert(1)">'
        "</p>"
    )
    assert 'style="color:red"' in sanitized
    assert "onclick" not in sanitized and "onerror" not in sanitized
    assert "javascript:" not in sanitized and "<script" not in sanitized
    assert 'src="https://example.test/image.png"' in sanitized


def test_built_in_comment_mail_uses_absolute_confirmation_links_and_optional_branding() -> None:
    identity = uuid4()
    origin = "https://blog.example.test"
    values = comment_data(
        {"actor": {"name": "<Reader>"}, "resource": {"title": "An article"}},
        str(identity),
    )
    assert values["resource"]["review_url"] == f"/admin/comments?comment={identity}"
    template = default_template("comment.pending_review", "email", "en")
    site = {
        "name": "My blog",
        "icon_url": f"{origin}/icon.png",
        "url": origin,
        "accent_color": "#3154d9",
    }
    body = sanitize_email_html(
        render_html_template(template.html, {**email_urls(values, origin), "site": site})
    )
    assert 'src="https://blog.example.test/icon.png"' in body
    assert "My blog" in body and "&lt;Reader&gt;" in body
    assert f'href="{origin}/admin/comments?comment={identity}&amp;action=approve"' in body
    assert f'href="{origin}/admin/comments?comment={identity}&amp;action=reject"' in body
    assert ">Help</a>" not in body
    fallback = sanitize_email_html(
        render_html_template(template.html, {**email_urls(values, ""), "site": {"name": "My blog"}})
    )
    assert "My blog" in fallback and "<img" not in fallback and "<a " not in fallback


def test_plugin_notification_platform_registration_is_atomic_and_namespaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def deliver(_: NotificationDeliveryRequest) -> NotificationDeliveryResult:
        return NotificationDeliveryResult(status="sent")

    platform = NotificationPlatform(
        id="test.bus",
        title="消息总线",
        description="Test bridge",
        kind="bridge",
        delivery_semantics="idempotent",
        deliver=deliver,
    )

    def setup(registry: Any) -> None:
        registry.notifications.register(platform)

    plugin = define_plugin(id="test.notifications", title="Notifications", setup=setup)
    catalog = collect_plugins([(plugin, "a" * 64)])
    assert catalog.notification_platform("test.bus") is platform
    assert catalog.plugins[0].info.notification_platforms == ["test.bus"]

    def duplicate(registry: Any) -> None:
        registry.notifications.register(platform)
        registry.notifications.register(platform)

    bad = define_plugin(id="test.duplicate", title="Duplicate", setup=duplicate)
    with pytest.raises(ValueError, match="Invalid notification platform"):
        collect_plugins([(bad, "b" * 64)])


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1",
    reason="Dedicated PostgreSQL test database required",
)
def test_routes_templates_inbox_and_uncertain_delivery_are_durable(tmp_path: Path) -> None:
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    name = "everunfold_notifications_test_" + uuid4().hex[:12]
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text())
    raw["database"]["url"] = original_url.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "test-notifications-" + uuid4().hex
    raw["frontends"] = {}
    config_file = tmp_path / "backend.local.toml"
    config_file.write_text(toml_text(raw))
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "REDIS_URL", "EVERUNFOLD_ADMIN_PASSWORD"}
    }
    settings = load_settings(config_file)
    username = "notify_" + uuid4().hex
    try:
        initialized = subprocess.run(
            [sys.executable, "init.database.py", "--config", str(config_file)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert initialized.returncode == 0, initialized.stderr
        asyncio.run(seed_admin(settings, username, PASSWORD))
        mail_profile_id = uuid4()

        async def configure_disabled_mail_profile() -> None:
            database = Database(settings.database.url.get_secret_value())
            try:
                async with database.session_factory() as session:
                    site = await session.get(SiteSettings, 1)
                    assert site is not None
                    site.data = {
                        **site.data,
                        "_mail_private": {
                            "revision": 1,
                            "profiles": [
                                {
                                    "id": str(mail_profile_id),
                                    "name": "通知邮箱",
                                    "enabled": False,
                                    "is_default": True,
                                    "value": {"enabled": False},
                                    "password": "",
                                }
                            ],
                        },
                    }
                    await session.commit()
            finally:
                await database.close()

        asyncio.run(configure_disabled_mail_profile())
        with ExitStack() as stack:
            client = stack.enter_context(
                TestClient(create_app(settings), client=("notification-test", 50000))
            )
            client.headers["Origin"] = ORIGIN
            session = signed_in(client, username)
            actor_id = UUID(session["user"]["id"])
            initial = client.get("/v1/admin/notifications/state")
            assert initial.status_code == 200, initial.text
            state = initial.json()["data"]
            assert state["revision"] == 1
            assert {item["id"] for item in state["platforms"]} >= {"in-app", "email"}
            assert state["routes"][0]["recipient_value"] == "comments:moderate"
            default_email = next(
                item
                for item in state["templates"]
                if item["event_type"] == "comment.pending_review"
                and item["platform_id"] == "email"
                and item["locale"] == "zh-CN"
            )
            assert default_email["html_enabled"] is True
            assert "<table" in default_email["html"]
            routes = {
                "expected_revision": 1,
                "routes": [
                    {
                        "event_type": "comment.pending_review",
                        "recipient_type": "permission",
                        "recipient_value": "comments:moderate",
                        "platform_id": "in-app",
                        "template_key": "default",
                        "enabled": True,
                    },
                    {
                        "event_type": "comment.pending_review",
                        "recipient_type": "role",
                        "recipient_value": "admin",
                        "platform_id": "in-app",
                        "template_key": "default",
                        "enabled": True,
                    },
                    {
                        "event_type": "comment.pending_review",
                        "recipient_type": "user",
                        "recipient_value": str(actor_id),
                        "platform_id": "email",
                        "mail_profile_id": str(mail_profile_id),
                        "template_key": "default",
                        "enabled": True,
                    },
                ],
            }
            saved = client.patch(
                "/v1/admin/notifications/routes", json=routes, headers=headers(client)
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["data"]["revision"] == 2

            template = client.patch(
                "/v1/admin/notifications/templates",
                json={
                    "expected_revision": 2,
                    "event_type": "comment.pending_review",
                    "platform_id": "in-app",
                    "locale": "zh-CN",
                    "subject": "新评论：{{resource.title}}",
                    "text": "{{actor.name}}：{{content.excerpt}}{{missing.value}}",
                    "enabled": True,
                    "reset": False,
                },
                headers=headers(client),
            )
            assert template.status_code == 200, template.text
            custom = next(
                item
                for item in template.json()["data"]["templates"]
                if item["event_type"] == "comment.pending_review"
                and item["platform_id"] == "in-app"
                and item["locale"] == "zh-CN"
            )
            assert custom["subject"] == "新评论：{{resource.title}}" and custom["custom"]
            html_template = client.patch(
                "/v1/admin/notifications/templates",
                json={
                    "expected_revision": 3,
                    "event_type": "comment.pending_review",
                    "platform_id": "email",
                    "locale": "zh-CN",
                    "subject": "HTML：{{resource.title}}",
                    "text": "{{actor.name}} 提交了评论",
                    "html": "<p>{{actor.name}}</p><script>alert(1)</script>",
                    "html_enabled": True,
                    "enabled": True,
                    "reset": False,
                },
                headers=headers(client),
            )
            assert html_template.status_code == 200, html_template.text
            assert html_template.json()["data"]["revision"] == 4
            brand_path = "/v1/admin/notifications/mail-brand"
            brand = {
                "site_url": "https://blog.example.test",
                "icon_url": "https://blog.example.test/icon.png",
            }
            branding = client.patch(
                brand_path, json={"expected_revision": 4, "brand": brand}, headers=headers(client)
            )
            assert branding.status_code == 200, branding.text
            brand_state = branding.json()["data"]
            assert brand_state["site_variables"]["icon_url"] == brand["icon_url"]
            existing = next(
                item
                for item in brand_state["templates"]
                if item["event_type"] == "comment.pending_review"
                and item["platform_id"] == "email"
                and item["locale"] == "zh-CN"
            )
            assert existing["html"].startswith("<p>{{actor.name}}</p>")
            assert "{{site.name}}" in existing["builtin"]["html"]

            async def exercise() -> None:
                database = Database(settings.database.url.get_secret_value())
                try:
                    async with database.session_factory() as session:
                        actor = await session.get(User, actor_id)
                        assert actor is not None
                        actor.email = "admin@example.test"
                        await session.execute(delete(User).where(User.id != actor_id))
                        await publish_event(
                            session,
                            event_type="comment.pending_review",
                            dedupe_key="notification-test:event",
                            resource_type="comment",
                            resource_id="event",
                            actor_id=None,
                            data={
                                "actor": {"name": "Reader"},
                                "content": {"excerpt": "Hello"},
                                "resource": {
                                    "title": "Article",
                                    "url": "/article",
                                    "review_url": "/admin/comments",
                                },
                            },
                        )
                        await session.commit()
                    async with database.session_factory() as session:
                        count = await NotificationService(session, settings).dispatch_events()
                        assert count == 2
                        deliveries = list(
                            await session.scalars(
                                select(NotificationDelivery).where(
                                    NotificationDelivery.user_id == actor_id
                                )
                            )
                        )
                        assert len(deliveries) == 2
                        assert (
                            len([item for item in deliveries if item.platform_id == "in-app"]) == 1
                        )
                        event = await session.scalar(
                            select(NotificationEvent).where(
                                NotificationEvent.dedupe_key == "notification-test:event"
                            )
                        )
                        assert event is not None
                        now = datetime.now(UTC)
                        for index in range(55):
                            identity = uuid5(
                                NAMESPACE_URL,
                                f"notification-test:inbox:{index}",
                            )
                            session.add(
                                NotificationEvent(
                                    id=identity,
                                    event_type="comment.pending_review",
                                    event_version=1,
                                    dedupe_key=f"notification-test:inbox:{index}",
                                    resource_type="comment",
                                    resource_id=str(index),
                                    actor_id=None,
                                    data={},
                                    occurred_at=now,
                                    dispatched_at=now,
                                )
                            )
                            session.add(
                                NotificationInbox(
                                    id=identity,
                                    event_id=identity,
                                    user_id=actor_id,
                                    title=f"Notification {index}",
                                    text="Body",
                                    href="",
                                    created_at=now - timedelta(seconds=index),
                                    read_at=now if index < 54 else None,
                                )
                            )
                        site = await session.get(SiteSettings, 1)
                        assert site is not None
                        site.data = {
                            **site.data,
                            "_mail_private": {
                                "revision": 1,
                                "value": {"enabled": False},
                                "password": "",
                            },
                        }
                        await session.commit()

                    async with database.session_factory() as session:
                        await deliver_notifications(settings)
                    async with database.session_factory() as session:
                        email = await session.scalar(
                            select(NotificationDelivery).where(
                                NotificationDelivery.user_id == actor_id,
                                NotificationDelivery.platform_id == "email",
                            )
                        )
                        assert (
                            email is not None
                            and email.status == "suppressed"
                            and email.mail_profile_id == str(mail_profile_id)
                        )
                        assert email.html == "<p>Reader</p>"
                        assert "script" not in email.html
                        audits = await session.scalar(
                            select(func.count(SettingsAudit.id)).where(
                                SettingsAudit.resource.in_(
                                    ["notification-routes", "notification-templates"]
                                )
                            )
                        )
                        assert audits == 3
                finally:
                    await database.close()

            asyncio.run(exercise())
            inbox = client.get("/v1/admin/notifications/inbox")
            assert inbox.status_code == 200, inbox.text
            inbox_data = inbox.json()["data"]
            assert len(inbox_data["items"]) == 50
            assert inbox_data["unread"] == 2
            marked = client.patch(
                "/v1/admin/notifications/inbox",
                json={"ids": []},
                headers=headers(client),
            )
            assert marked.status_code == 200 and marked.json()["data"]["unread"] == 0

            async def stale() -> None:
                database = Database(settings.database.url.get_secret_value())
                try:
                    async with database.session_factory() as session:
                        now = datetime.now(UTC)
                        event_id = uuid5(NAMESPACE_URL, "notification-test:stale-event")
                        session.add(
                            NotificationEvent(
                                id=event_id,
                                event_type="comment.pending_review",
                                event_version=1,
                                dedupe_key="notification-test:stale-event",
                                resource_type="comment",
                                resource_id="stale",
                                actor_id=None,
                                data={},
                                occurred_at=now,
                                dispatched_at=now,
                            )
                        )
                        await session.commit()
                    async with database.session_factory() as session:
                        now = datetime.now(UTC)
                        event_id = uuid5(NAMESPACE_URL, "notification-test:stale-event")
                        session.add(
                            NotificationDelivery(
                                id=uuid5(NAMESPACE_URL, "notification-test:stale-delivery"),
                                event_id=event_id,
                                user_id=actor_id,
                                platform_id="email",
                                template_id=None,
                                locale="zh-CN",
                                subject="Stale",
                                text="Stale",
                                html="",
                                href="",
                                destination="admin@example.test",
                                status="sending",
                                attempts=1,
                                next_attempt_at=now,
                                lease_id=uuid4(),
                                lease_until=now - timedelta(minutes=2),
                                error=None,
                                created_at=now,
                                finished_at=None,
                            )
                        )
                        await session.commit()
                finally:
                    await database.close()

            asyncio.run(stale())
            asyncio.run(deliver_notifications(settings))

            async def stale_result() -> None:
                database = Database(settings.database.url.get_secret_value())
                try:
                    async with database.session_factory() as session:
                        row = await session.scalar(
                            select(NotificationDelivery).where(
                                NotificationDelivery.event_id
                                == uuid5(NAMESPACE_URL, "notification-test:stale-event")
                            )
                        )
                        assert row is not None
                        assert row.status == "unknown"
                        assert row.error == "execution_lease_expired"
                finally:
                    await database.close()

            asyncio.run(stale_result())
            delivery_event_id = uuid4()

            async def settings_delivery_failure() -> None:
                database = Database(settings.database.url.get_secret_value())
                try:
                    async with database.session_factory() as session:
                        site = await session.get(SiteSettings, 1)
                        assert site is not None
                        now = datetime.now(UTC)
                        session.add(
                            SettingsDeliveryEvent(
                                id=delivery_event_id,
                                node_id="offline-node",
                                source_epoch=site.source_epoch,
                                resource="public-settings",
                                revision=999,
                                payload={
                                    "resource": "public-settings",
                                    "source_epoch": str(site.source_epoch),
                                    "revision": 999,
                                },
                                attempts=settings.outbox.max_attempts - 1,
                                next_attempt_at=now,
                            )
                        )
                        await session.commit()
                    await deliver_batch(settings)
                    async with database.session_factory() as session:
                        event = await session.scalar(
                            select(NotificationEvent).where(
                                NotificationEvent.dedupe_key
                                == f"settings-delivery-failed:{delivery_event_id}"
                            )
                        )
                        assert event is not None
                        assert event.event_type == "settings.delivery_failed"
                        assert event.data["node"]["id"] == "offline-node"
                        assert event.data["settings"]["resource"] == "public-settings"
                finally:
                    await database.close()

            asyncio.run(settings_delivery_failure())
    finally:

        async def clean() -> None:
            connection = await asyncpg.connect(
                original_url.set(database="postgres", drivername="postgresql").render_as_string(
                    hide_password=False
                )
            )
            try:
                await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await connection.close()

        asyncio.run(clean())
