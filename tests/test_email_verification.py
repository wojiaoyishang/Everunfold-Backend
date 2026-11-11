"""One-time account email verification with a local SMTP server and isolated database."""

import asyncio
import os
import subprocess
import sys
import tomllib
from email import message_from_bytes
from email import policy as email_policy
from pathlib import Path
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from anyio import CapacityLimiter
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.accounts_schemas import (
    AccountProfile,
    EmailVerificationConfirm,
    EmailVerificationRequest,
    ProfileFields,
    ProfileSave,
)
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.bootstrap import seed_admin
from app.modules.auth.email_verification_delivery import deliver
from app.modules.auth.email_verification_models import EmailVerification
from app.modules.auth.email_verification_service import EmailVerificationService
from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure, AuthService
from app.modules.settings.mail_schemas import SaveMailSettings
from app.modules.settings.mail_service import MailSettingsService
from app.modules.settings.service import SettingsService
from tests.test_accounts import PASSWORD
from tests.test_mail_delivery import preferences, smtp_server
from tests.test_settings_processes import toml_text

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1",
    reason="Dedicated PostgreSQL test database required",
)
def test_email_verification_is_one_time_rate_limited_and_clears_after_change(
    tmp_path: Path,
) -> None:
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    name = "everunfold_email_verify_" + uuid4().hex[:12]
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text())
    raw["database"]["url"] = original_url.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "email-verify-" + uuid4().hex
    raw["frontends"] = {}
    config_file = tmp_path / "backend.local.toml"
    config_file.write_text(toml_text(raw))
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "REDIS_URL", "EVERUNFOLD_ADMIN_PASSWORD"}
    }
    settings = load_settings(config_file)
    username = "verify_" + uuid4().hex
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
        with smtp_server() as (port, messages):

            async def exercise() -> None:
                database = Database(settings.database.url.get_secret_value())
                try:
                    async with database.session_factory() as session:
                        user = await session.scalar(select(User).where(User.username == username))
                        assert user is not None
                        await MailSettingsService(SettingsService(session, settings)).save(
                            SaveMailSettings(
                                expected_revision=0,
                                value=preferences(port, enabled=True),
                            ),
                            user.id,
                        )
                    operation_id = uuid4()
                    async with database.session_factory() as session:
                        user = await session.scalar(select(User).where(User.username == username))
                        assert user is not None
                        requested = await EmailVerificationService(session).request(
                            user,
                            EmailVerificationRequest(
                                operation_id=operation_id,
                                email="verified@example.test",
                            ),
                        )
                        assert requested.status == "queued"
                        row = await session.get(EmailVerification, operation_id)
                        assert row is not None and row.delivery_code.isdigit()
                        code = row.delivery_code
                        with pytest.raises(AuthFailure) as limited:
                            await EmailVerificationService(session).request(
                                user,
                                EmailVerificationRequest(
                                    operation_id=uuid4(),
                                    email="other@example.test",
                                ),
                            )
                        assert limited.value.status == 429
                    await deliver(settings)
                    assert len(messages) == 1
                    message = message_from_bytes(messages[0], policy=email_policy.default)
                    assert code in message.get_content()
                    async with database.session_factory() as session:
                        row = await session.get(EmailVerification, operation_id)
                        assert row is not None
                        assert row.status == "sent" and row.delivery_code == ""
                        user = await session.scalar(select(User).where(User.username == username))
                        assert user is not None
                        wrong = "000000" if code != "000000" else "111111"
                        with pytest.raises(AuthFailure):
                            await EmailVerificationService(session).confirm(
                                user,
                                EmailVerificationConfirm(
                                    request_id=operation_id,
                                    email="verified@example.test",
                                    code=wrong,
                                ),
                            )
                    async with database.session_factory() as session:
                        user = await session.scalar(select(User).where(User.username == username))
                        assert user is not None
                        verified = await EmailVerificationService(session).confirm(
                            user,
                            EmailVerificationConfirm(
                                request_id=operation_id,
                                email="verified@example.test",
                                code=code,
                            ),
                        )
                        assert isinstance(verified, AccountProfile)
                        assert verified.email == "verified@example.test"
                        assert verified.email_verified is True
                    async with database.session_factory() as session:
                        user = await session.scalar(select(User).where(User.username == username))
                        assert user is not None
                        auth = AuthService(
                            session,
                            settings.auth,
                            CapacityLimiter(2),
                        )
                        changed = await AccountsService(session, auth).save_profile(
                            user,
                            ProfileSave(
                                operation_id=uuid4(),
                                expected_revision=user.revision,
                                value=ProfileFields(
                                    display_name=user.display_name,
                                    email="changed@example.test",
                                ),
                            ),
                        )
                        assert changed.email_verified is False
                finally:
                    await database.close()

            asyncio.run(exercise())
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
