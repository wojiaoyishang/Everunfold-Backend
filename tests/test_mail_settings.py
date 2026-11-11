"""Private SMTP state, validation, write-only credentials and concurrent updates."""

import asyncio
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.settings.defaults import public_settings, stored_settings
from app.modules.settings.mail_schemas import (
    MailPreferences,
    MailSenderProfileInput,
    SaveMailSettings,
)
from app.modules.settings.mail_service import MAIL_KEY, MailSettingsService
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService
from tests.test_settings_processes import toml_text


@pytest.mark.parametrize(
    "invalid",
    [
        {"host": "smtp://mail.example"},
        {"host": "mail.example:587"},
        {"host": "user:secret@mail.example"},
        {"port": 0},
        {"port": 65536},
        {"encryption": "auto"},
        {"from_name": "sender\r\nBcc: hidden@example.com"},
        {"from_address": "Sender <test@example.com>"},
        {"reply_to": "not-email"},
    ],
)
def test_mail_rejects_malformed_configuration(invalid):
    with pytest.raises(ValidationError):
        MailPreferences.model_validate(invalid)


def test_mail_secret_never_enters_public_projection_or_repr():
    original = {MAIL_KEY: {"value": {"host": "private.example"}, "password": "mail-secret"}}
    public = public_settings(original)
    assert "mail-secret" not in public.model_dump_json()
    assert "private.example" not in public.model_dump_json()
    assert stored_settings(original, public)[MAIL_KEY] == original[MAIL_KEY]
    request = SaveMailSettings(
        expected_revision=0,
        profiles=[
            MailSenderProfileInput(
                id=uuid4(),
                name="系统通知",
                is_default=True,
                value=MailPreferences(),
                password=SecretStr("mail-secret"),
            )
        ],
    )
    assert "mail-secret" not in repr(request)
    assert "mail-secret" not in request.model_dump_json()


@pytest.mark.skipif(os.getenv("RUN_DATABASE_TESTS") != "1", reason="Requires isolated database")
def test_mail_password_lifecycle_and_revision_conflict(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = load_settings(root / "config.test.local.toml")
    base = make_url(config.database.url.get_secret_value())
    assert base.database == "everunfold_test"
    name = "everunfold_mail_test_" + uuid4().hex[:10]
    raw = tomllib.loads((root / "config.test.local.toml").read_text())
    raw["database"]["url"] = base.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "mail-test-" + uuid4().hex
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
    try:
        subprocess.run(
            [sys.executable, "init.database.py", "--config", str(path)],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )
        local = load_settings(path)

        async def check():
            db = Database(local.database.url.get_secret_value())
            actor = uuid4()

            async def save(payload):
                async with db.session_factory() as session:
                    return await MailSettingsService(SettingsService(session, local)).save(
                        payload, actor
                    )

            try:
                async with db.session_factory() as session:
                    state = await MailSettingsService(SettingsService(session, local)).state()
                    assert state.revision == 0 and state.profiles == []
                value = MailPreferences(
                    host="smtp.example.com",
                    from_address="sender@example.com",
                    username="sender",
                    enabled=True,
                )
                profile_id = uuid4()

                def profile_payload(**changes):
                    return MailSenderProfileInput(
                        id=profile_id,
                        name="系统通知",
                        is_default=True,
                        value=value,
                        password=SecretStr("sentinel-secret"),
                        **changes,
                    )

                payload = SaveMailSettings(
                    expected_revision=0, profiles=[profile_payload(enabled=True)]
                )
                outcomes = await asyncio.gather(
                    save(payload), save(payload), return_exceptions=True
                )
                failures = [x for x in outcomes if isinstance(x, AuthFailure)]
                assert len(failures) == 1 and failures[0].status == 409
                state = next(x for x in outcomes if not isinstance(x, Exception))
                assert state.revision == 1 and state.profiles[0].password_configured
                assert "sentinel-secret" not in state.model_dump_json()
                state = await save(
                    SaveMailSettings(
                        expected_revision=1,
                        profiles=[
                            MailSenderProfileInput(
                                id=profile_id,
                                name="系统通知",
                                enabled=True,
                                is_default=True,
                                value=value,
                            )
                        ],
                    )
                )
                assert state.profiles[0].password_configured
                async with db.session_factory() as session:
                    row = await session.get(SiteSettings, 1)
                    profile = row.data[MAIL_KEY]["profiles"][0]
                    assert profile["password"] == "sentinel-secret"
                    assert "sentinel-secret" not in public_settings(row.data).model_dump_json()
                    audits = list(
                        await session.scalars(
                            select(SettingsAudit).where(SettingsAudit.resource == "mail")
                        )
                    )
                    assert len(audits) == 2
                    assert "sentinel-secret" not in json.dumps([a.changes for a in audits])
                with pytest.raises(AuthFailure):
                    await save(
                        SaveMailSettings(
                            expected_revision=2,
                            profiles=[
                                MailSenderProfileInput(
                                    id=profile_id,
                                    name="系统通知",
                                    enabled=True,
                                    is_default=True,
                                    value=value,
                                    clear_password=True,
                                )
                            ],
                        )
                    )
                second_id = uuid4()
                state = await save(
                    SaveMailSettings(
                        expected_revision=2,
                        profiles=[
                            MailSenderProfileInput(
                                id=profile_id,
                                name="系统通知",
                                enabled=False,
                                value=MailPreferences(),
                                clear_password=True,
                            ),
                            MailSenderProfileInput(
                                id=second_id,
                                name="客服邮箱",
                                enabled=False,
                                is_default=True,
                                value=MailPreferences(from_address="support@example.com"),
                            ),
                        ],
                    )
                )
                assert state.revision == 3 and len(state.profiles) == 2
                assert state.profiles[1].id == second_id and state.profiles[1].is_default
                assert not state.profiles[0].password_configured
            finally:
                await db.close()

        asyncio.run(check())
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
