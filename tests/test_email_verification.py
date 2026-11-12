"""One-time account email verification with a local SMTP server and isolated database."""

import asyncio
import os
import re
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email import policy as email_policy
from pathlib import Path
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from anyio import CapacityLimiter
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

from app.core.config import Settings, load_settings
from app.core.database import Database
from app.entrypoints.api import create_app
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
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.registration_delivery import deliver as deliver_registration
from app.modules.auth.service import AuthFailure, AuthService
from app.modules.settings.mail_schemas import MailSenderProfileInput, SaveMailSettings
from app.modules.settings.mail_service import MailSettingsService
from app.modules.settings.service import SettingsService
from tests.test_accounts import ORIGIN, PASSWORD, headers, signed_in
from tests.test_mail_delivery import preferences, smtp_server
from tests.test_settings_processes import toml_text

ROOT = Path(__file__).resolve().parents[1]


async def verify_invitation_registration(
    admin: TestClient, settings: Settings, messages: list[bytes]
) -> None:
    registration = "/v1/auth/registration"
    invitations = "/v1/admin/access/invitations"
    access_path = "/v1/admin/access/settings"
    access = admin.get(access_path).json()["data"]
    assert admin.get(registration + "/options").json()["data"]["mode"] == "closed"
    policy = {
        "expected_revision": access["revision"],
        "login_captcha": access["login_captcha"],
        "email_verification_captcha": access["email_verification_captcha"],
        "mail_profile_id": access["mail_profile_id"],
        "registration_mode": "invite",
        "registration_captcha": True,
    }
    saved = admin.patch(access_path, json=policy, headers=headers(admin))
    assert saved.status_code == 200, saved.text
    issued = admin.post(invitations, json={"days_valid": 1}, headers=headers(admin))
    assert issued.status_code == 200
    invite = issued.json()["data"]
    assert invite["code"] not in admin.get(invitations).text
    suffix = uuid4().hex[:10]
    with (
        TestClient(create_app(settings)) as first,
        TestClient(create_app(settings)) as second,
    ):
        codes = []
        for index, browser in enumerate((first, second)):
            payload = {
                "username": f"QA_{suffix}_{index}",
                "display_name": "Registration QA",
                "email": f"QA_{suffix}_{index}@example.test",
                "password": PASSWORD,
                "invite_code": invite["code"],
            }
            req_headers = headers(browser)
            assert (
                browser.post(
                    registration + "/request", json=payload, headers=req_headers
                ).status_code
                == 403
            )
            client_id = str(uuid4())
            challenge = browser.post(
                "/v1/reader/captcha/challenge",
                json={"action": "registration", "client_id": client_id},
                headers={"Origin": ORIGIN},
            ).json()["data"]
            queued = browser.post(
                registration + "/request",
                json={
                    **payload,
                    "captcha": {
                        "challenge_id": challenge["challenge_id"],
                        "client_id": client_id,
                        "answer": "QA42",
                    },
                },
                headers=req_headers,
            )
            assert queued.status_code == 200, queued.text
            assert queued.json()["data"]["status"] == "queued"
            assert not browser.get("/v1/auth/session").json()["data"]["authenticated"]
            assert await deliver_registration(settings) == {"processed": 1}
            status = browser.get(registration + "/status").json()["data"]
            assert status["status"] == "sent"
            content = message_from_bytes(messages[-1], policy=email_policy.default)
            code = re.search(r"[0-9]{6}", content.get_body(preferencelist=("plain",)).get_content())
            assert code is not None
            codes.append({"request_id": status["request_id"], "code": code.group()})
        results = await asyncio.gather(
            asyncio.to_thread(
                first.post, registration + "/confirm", json=codes[0], headers=headers(first)
            ),
            asyncio.to_thread(
                second.post, registration + "/confirm", json=codes[1], headers=headers(second)
            ),
        )
        assert sorted(response.status_code for response in results) == [200, 422]
        winner = next(i for i, response in enumerate(results) if response.status_code == 200)
        user = results[winner].json()["data"]["user"]
        assert user["username"] == f"qa_{suffix}_{winner}"
        assert user["role"] == "user" and user["permissions"] == {}
        browser = (first, second)[winner]
        assert browser.get("/v1/account/profile").json()["data"]["email_verified"] is True
        assert browser.get("/v1/admin/session").status_code == 403
        assert (
            browser.post(
                registration + "/confirm", json=codes[winner], headers=headers(browser)
            ).status_code
            == 422
        )
    assert admin.get(invitations).json()["data"]["items"][0]["consumed_at"] is not None


@pytest.mark.skipif(
    os.environ.get("RUN_DATABASE_TESTS") != "1",
    reason="Dedicated PostgreSQL test database required",
)
def test_email_verification_is_one_time_rate_limited_and_clears_after_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_settings(ROOT / "config.test.local.toml")
    original_url = make_url(original.database.url.get_secret_value())
    assert original_url.database == "everunfold_test"
    name = "everunfold_email_verify_" + uuid4().hex[:12]
    raw = tomllib.loads((ROOT / "config.test.local.toml").read_text())
    raw["database"]["url"] = original_url.set(database=name).render_as_string(hide_password=False)
    raw["redis"]["namespace_scope"] = "email-verify-" + uuid4().hex
    raw["frontends"] = {}
    raw["cors"]["allowed_origins"] = [ORIGIN]
    config_file = tmp_path / "backend.local.toml"
    config_file.write_text(toml_text(raw))
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "REDIS_URL", "EVERUNFOLD_ADMIN_PASSWORD"}
    }
    settings = load_settings(config_file)
    username = "verify_" + uuid4().hex
    delegated_name = username + "_manager"
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
        asyncio.run(seed_admin(settings, delegated_name, PASSWORD))
        monkeypatch.setattr(
            "app.modules.captcha.service.generate_image",
            lambda: ("QA42", "data:image/png;base64,AA=="),
        )
        with (
            smtp_server() as (port, messages),
            TestClient(create_app(settings)) as client,
        ):
            signed_in(client, username)
            access_path = "/v1/admin/access/settings"
            request_path = "/v1/account/email-verification/request"
            request_prefix = "/v1/account/email-verification/"
            captcha_path = "/v1/reader/captcha/challenge"

            def email_captcha() -> dict[str, str]:
                client_id = str(uuid4())
                response = client.post(
                    captcha_path,
                    json={"action": "email_verification", "client_id": client_id},
                    headers={"Origin": ORIGIN},
                )
                assert response.status_code == 200
                data = response.json()["data"]
                assert data["required"] is True
                return {
                    "challenge_id": data["challenge_id"],
                    "client_id": client_id,
                    "answer": "QA42",
                }

            async def exercise() -> None:
                database = Database(settings.database.url.get_secret_value())
                try:
                    async with database.session_factory() as session:
                        user = await session.scalar(select(User).where(User.username == username))
                        assert user is not None
                        await MailSettingsService(SettingsService(session, settings)).save(
                            SaveMailSettings(
                                expected_revision=0,
                                profiles=[
                                    MailSenderProfileInput(
                                        id=uuid4(),
                                        name="系统通知",
                                        enabled=True,
                                        is_default=True,
                                        value=preferences(port, enabled=True),
                                    )
                                ],
                            ),
                            user.id,
                        )
                        await session.execute(
                            update(User)
                            .where(User.username == delegated_name)
                            .values(role="user", grants={"everunfold": {"users:manage": "all"}})
                        )
                        await session.commit()
                    operation_id = uuid4()
                    template_state = client.get("/v1/admin/notifications/state").json()["data"]
                    assert any(
                        item["id"] == "security.email_verification"
                        for item in template_state["events"]
                    )
                    template = {
                        "expected_revision": template_state["revision"],
                        "event_type": "security.email_verification",
                        "platform_id": "email",
                        "locale": "zh-CN",
                        "subject": "自定义邮箱验证",
                        "text": "本地测试码：{{verification.code}}",
                        "html": "<p>测试码：<strong>{{verification.code}}</strong></p>",
                        "html_enabled": True,
                        "enabled": True,
                    }
                    invalid = client.patch(
                        "/v1/admin/notifications/templates",
                        json={**template, "text": "没有验证码"},
                        headers=headers(client),
                    )
                    assert invalid.status_code == 422
                    for unsafe_html in (
                        "<p>没有验证码</p>",
                        '<img src="https://example.test/{{verification.code}}">',
                        "<script>{{verification.code}}</script>",
                    ):
                        invalid = client.patch(
                            "/v1/admin/notifications/templates",
                            json={**template, "html": unsafe_html},
                            headers=headers(client),
                        )
                        assert invalid.status_code == 422
                    saved_template = client.patch(
                        "/v1/admin/notifications/templates",
                        json=template,
                        headers=headers(client),
                    )
                    assert saved_template.status_code == 200, saved_template.text
                    access = client.get(access_path).json()["data"]
                    sender = access["mail_profiles"][0]["id"]
                    policy = {
                        "expected_revision": access["revision"],
                        "login_captcha": False,
                        "email_verification_captcha": True,
                        "mail_profile_id": sender,
                    }
                    saved = client.patch(access_path, json=policy, headers=headers(client))
                    assert saved.status_code == 200
                    assert saved.json()["data"]["mail_profile_id"] == sender
                    assert (
                        client.patch(access_path, json=policy, headers=headers(client)).status_code
                        == 409
                    )
                    payload = {"operation_id": str(operation_id), "email": "verified@example.test"}
                    refused = client.post(request_path, json=payload, headers=headers(client))
                    assert refused.status_code == 403
                    queued = client.post(
                        request_path,
                        json={**payload, "captcha": email_captcha()},
                        headers=headers(client),
                    )
                    assert queued.status_code == 200
                    assert queued.json()["data"]["status"] == "queued"
                    status_path = request_prefix + str(operation_id)
                    assert client.get(status_path).json()["data"]["status"] == "queued"
                    retry = client.post(
                        request_path,
                        json={**payload, "operation_id": str(uuid4()), "captcha": email_captcha()},
                        headers=headers(client),
                    )
                    assert retry.status_code == 429 and 0 < int(retry.headers["Retry-After"]) <= 60
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
                    assert client.get(status_path).json()["data"]["status"] == "sent"
                    message = message_from_bytes(messages[0], policy=email_policy.default)
                    assert message["Subject"] == "自定义邮箱验证"
                    assert code in message.get_body(preferencelist=("plain",)).get_content()
                    assert code in message.get_body(preferencelist=("html",)).get_content()
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
                        assert verified.email_verification_source == "mailbox"
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
                    review = f"/v1/admin/users/{changed.id}/email-verification"
                    with TestClient(create_app(settings)) as delegated:
                        member = signed_in(delegated, delegated_name)["user"]
                        assert delegated.get("/v1/admin/users").status_code == 200
                        assert delegated.get(access_path).status_code == 403
                        member_path = f"/v1/admin/users/{member['id']}"
                        member_revision = delegated.get(member_path).json()["data"]["revision"]
                        forbidden = delegated.post(
                            member_path + "/email-verification",
                            json={
                                "operation_id": str(uuid4()),
                                "expected_revision": member_revision,
                                "action": "approve",
                                "reason": "Ordinary user management does not permit this",
                            },
                            headers=headers(delegated),
                        )
                        assert forbidden.status_code == 403
                        assert forbidden.json()["code"] == 40374
                    approved = client.post(
                        review,
                        json={
                            "operation_id": str(uuid4()),
                            "expected_revision": changed.revision,
                            "action": "approve",
                            "reason": "Verified through support",
                        },
                        headers=headers(client),
                    )
                    assert approved.status_code == 200
                    approval = approved.json()["data"]
                    assert approval["email_verification_source"] == "admin"
                    assert approval["email_verified"] is False
                    async with database.session_factory() as session:
                        await session.execute(
                            update(EmailVerification)
                            .where(EmailVerification.id == operation_id)
                            .values(created_at=datetime.now(UTC) - timedelta(minutes=2))
                        )
                        await session.commit()
                    replacement = client.post(
                        request_path,
                        json={**payload, "operation_id": str(uuid4()), "captcha": email_captcha()},
                        headers=headers(client),
                    )
                    assert replacement.status_code == 200
                    revoked = client.post(
                        review,
                        json={
                            "operation_id": str(uuid4()),
                            "expected_revision": approval["revision"],
                            "action": "revoke",
                            "reason": "Mailbox control lost",
                        },
                        headers=headers(client),
                    )
                    assert revoked.status_code == 200
                    assert revoked.json()["data"]["email_verification_source"] is None
                    replacement_id = replacement.json()["data"]["request_id"]
                    assert (
                        client.get(request_prefix + replacement_id).json()["data"]["status"]
                        == "expired"
                    )
                    assert await deliver(settings) == {"processed": 0}
                    async with database.session_factory() as session:
                        audit = await session.scalar(
                            select(AuthAuditEvent).where(
                                AuthAuditEvent.target_user_id == changed.id,
                                AuthAuditEvent.action == "email_review_revoke",
                            )
                        )
                        assert audit is not None and audit.user_id == changed.id
                        assert audit.details["reason"] == "Mailbox control lost"
                finally:
                    await database.close()

            asyncio.run(exercise())
            asyncio.run(verify_invitation_registration(client, settings, messages))
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
