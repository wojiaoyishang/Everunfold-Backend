"""Local SMTP and real Worker/Scheduler delivery; no external mail provider."""

import asyncio
import os
import socketserver
import subprocess
import sys
import threading
import tomllib
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from redis import Redis
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.core.jobs.progress import watch_progress
from app.core.jobs.progress_schemas import WatchTask
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.settings.mail_delivery import deliver_test
from app.modules.settings.mail_schemas import MailPreferences, SaveMailSettings, SendTestMail
from app.modules.settings.mail_service import MailSettingsService
from app.modules.settings.mail_test_service import MailTestService
from app.modules.settings.mail_transport import send_test
from app.modules.settings.models import MailTestDelivery
from app.modules.settings.service import SettingsService
from tests.test_settings_processes import background, toml_text, wait_for


@contextmanager
def smtp_server(*, reject=False, drop=False):
    messages = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(10)
            self.wfile.write(b"220 localhost test SMTP\r\n")
            body = None
            while line := self.rfile.readline():
                if body is not None:
                    if line == b".\r\n":
                        messages.append(bytes(body))
                        if drop:
                            return
                        self.wfile.write(b"250 accepted\r\n")
                        body = None
                    else:
                        body.extend(line)
                elif line.upper().startswith((b"EHLO", b"HELO")):
                    self.wfile.write(b"250 localhost\r\n")
                elif line.upper().startswith(b"RCPT") and reject:
                    self.wfile.write(b"550 sentinel-do-not-expose\r\n")
                elif line.upper().startswith(b"DATA"):
                    self.wfile.write(b"354 go ahead\r\n")
                    body = bytearray()
                else:
                    self.wfile.write(b"250 OK\r\n")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], messages
        finally:
            server.shutdown()
            thread.join(timeout=3)


def preferences(port, **changes):
    return MailPreferences(
        host="127.0.0.1",
        port=port,
        encryption="none",
        authenticate=False,
        from_address="sender@example.test",
        **changes,
    )


def test_smtp_delivery_acceptance_and_uncertainty():
    with smtp_server() as (port, messages):
        assert send_test(preferences(port), "", "reader@example.test", uuid4()) == ("sent", None)
        assert len(messages) == 1 and b"To: reader@example.test\r\n" in messages[0]
        assert b"Message-ID:" in messages[0]
    with smtp_server(reject=True) as (port, messages):
        assert send_test(preferences(port), "", "reader@example.test", uuid4()) == (
            "failed",
            "recipient_rejected",
        )
        assert messages == []
    with smtp_server(drop=True) as (port, messages):
        assert send_test(preferences(port), "", "reader@example.test", uuid4()) == (
            "unknown",
            "delivery_unknown",
        )
        assert len(messages) == 1


def test_starttls_never_falls_back_to_plaintext():
    with smtp_server() as (port, messages):
        value = preferences(port).model_copy(update={"encryption": "starttls"})
        assert send_test(value, "sentinel-password", "reader@example.test", uuid4()) == (
            "failed",
            "unsupported",
        )
        assert messages == []


@pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1", reason="Requires isolated database and Redis"
)
def test_scheduler_worker_test_mail_idempotency_and_config_fence(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = load_settings(root / "config.test.local.toml")
    base = make_url(original.database.url.get_secret_value())
    assert base.database == "everunfold_test"
    name = "everunfold_mail_delivery_" + uuid4().hex[:10]
    raw = tomllib.loads((root / "config.test.local.toml").read_text())
    raw["database"]["url"] = base.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "mail-delivery-" + uuid4().hex
    raw["frontends"] = {}
    path = tmp_path / "config.toml"
    path.write_text(toml_text(raw))
    env = {**os.environ, "CONFIG_FILE": str(path)}
    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "EVERUNFOLD_ADMIN_PASSWORD",
        "EVERUNFOLD_ADMIN_USERNAME",
    ):
        env.pop(key, None)
    local = load_settings(path)
    actor, identity = uuid4(), uuid4()
    try:
        subprocess.run(
            [sys.executable, "init.database.py", "--config", str(path)],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )
        with smtp_server() as (port, messages):

            async def prepare():
                db = Database(local.database.url.get_secret_value())
                try:
                    async with db.session_factory() as session:
                        await MailSettingsService(SettingsService(session, local)).save(
                            SaveMailSettings(expected_revision=0, value=preferences(port)), actor
                        )
                    payload = SendTestMail(
                        request_id=identity, expected_revision=1, recipient="reader@example.test"
                    )

                    async def request():
                        async with db.session_factory() as session:
                            return await MailTestService(SettingsService(session, local)).request(
                                payload, actor
                            )

                    results = await asyncio.gather(request(), request())
                    assert [r.id for r in results] == [identity, identity]
                    async with db.session_factory() as session:
                        with pytest.raises(AuthFailure) as missing:
                            await MailTestService(SettingsService(session, local)).read(
                                identity, uuid4()
                            )
                        assert missing.value.status == 404
                finally:
                    await db.close()

            asyncio.run(prepare())

            async def status():
                db = Database(local.database.url.get_secret_value())
                try:
                    async with db.session_factory() as session:
                        return (await session.get(MailTestDelivery, identity)).status
                finally:
                    await db.close()

            with ExitStack() as stack:
                stack.enter_context(
                    background([sys.executable, "-m", "app.entrypoints.scheduler"], env, root)
                )
                stack.enter_context(
                    background(
                        [
                            sys.executable,
                            "-m",
                            "celery",
                            "-A",
                            "app.entrypoints.worker:celery_app",
                            "worker",
                            "--pool=solo",
                            "--loglevel=WARNING",
                        ],
                        env,
                        root,
                    )
                )
                wait_for(lambda: asyncio.run(status()) == "sent", timeout=35)
                assert len(messages) == 1

                async def diagnostics():
                    owner = Actor(actor, "test", "Test", "admin", {"settings:write": "all"})
                    progress = await watch_progress(
                        local,
                        owner,
                        WatchTask(
                            resource="mail-test",
                            id=identity,
                            wait_seconds=0,
                        ),
                    )
                    assert progress.state == "succeeded"
                    assert progress.events[0].message == "Test message queued for Worker delivery."
                    assert [event.sequence for event in progress.events] == sorted(
                        {event.sequence for event in progress.events}
                    )
                    transcript = "\n".join(event.message for event in progress.events)
                    assert "EHLO" in transcript and "250" in transcript
                    assert "Message-ID:" in transcript and "This test email" in transcript
                    assert "SMTP server accepted" in transcript
                    resumed = await watch_progress(
                        local,
                        owner,
                        WatchTask(
                            resource="mail-test",
                            id=identity,
                            after_sequence=progress.cursor,
                            wait_seconds=0,
                        ),
                    )
                    assert resumed.events == [] and resumed.state == "succeeded"

                asyncio.run(diagnostics())
            # A repeated worker drain never repeats a completed request.
            asyncio.run(deliver_test(local))
            assert len(messages) == 1

            async def changed():
                db = Database(local.database.url.get_secret_value())
                try:
                    async with db.session_factory() as session:
                        pending = await MailTestService(SettingsService(session, local)).request(
                            SendTestMail(
                                request_id=uuid4(),
                                expected_revision=1,
                                recipient="reader@example.test",
                            ),
                            actor,
                        )
                    async with db.session_factory() as session:
                        await MailSettingsService(SettingsService(session, local)).save(
                            SaveMailSettings(expected_revision=1, value=preferences(port)), actor
                        )
                    await deliver_test(local)
                    async with db.session_factory() as session:
                        row = await session.get(MailTestDelivery, pending.id)
                        assert row.status == "failed" and row.error == "configuration_changed"
                        assert len(list(await session.scalars(select(MailTestDelivery)))) == 2
                finally:
                    await db.close()

            asyncio.run(changed())
            assert len(messages) == 1
    finally:

        async def drop():
            conn = await asyncpg.connect(
                base.set(drivername="postgresql").render_as_string(hide_password=False)
            )
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
        redis = Redis.from_url(local.redis.url.get_secret_value())
        keys = list(redis.scan_iter(match=local.redis_prefix + "*"))
        if keys:
            redis.delete(*keys)
        redis.close()
