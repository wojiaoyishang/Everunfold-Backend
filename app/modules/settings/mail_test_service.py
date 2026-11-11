"""Only explicitly requested tests enter this queue; configuration saves never send."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from app.core.jobs.progress_models import TaskProgressEventRecord
from app.modules.auth.service import AuthFailure
from app.modules.settings.mail_schemas import MailTestState, SendTestMail
from app.modules.settings.mail_service import mail_profile, mail_ready, stored_mail_profiles
from app.modules.settings.models import MailTestDelivery, SettingsAudit
from app.modules.settings.service import SettingsService


def test_state(row: MailTestDelivery) -> MailTestState:
    return MailTestState.model_validate(
        {key: getattr(row, key) for key in MailTestState.model_fields}
    )


class MailTestService:
    def __init__(self, settings: SettingsService) -> None:
        self.settings = settings

    async def request(self, payload: SendTestMail, actor: UUID) -> MailTestState:
        site = await self.settings.site(locked=True)
        session = self.settings.session
        previous = await session.get(MailTestDelivery, payload.request_id)
        if previous:
            if (
                previous.user_id != actor
                or previous.recipient != payload.recipient
                or previous.revision != payload.expected_revision
                or previous.profile_id != str(payload.profile_id)
            ):
                raise AuthFailure(409, 40999, "测试请求编号已被使用")
            return test_state(previous)
        revision, _ = stored_mail_profiles(site.data)
        if revision != payload.expected_revision:
            raise AuthFailure(409, 40999, "邮件配置已改变，请重新读取后测试")
        profile = mail_profile(site.data, payload.profile_id)
        if profile is None:
            raise AuthFailure(422, 42299, "所选发信配置不存在")
        if not profile.enabled or not mail_ready(profile.value, profile.password):
            raise AuthFailure(422, 42299, "请先保存完整的SMTP服务器、发件邮箱和认证信息")
        now = datetime.now(UTC)
        count = await session.scalar(
            select(func.count())
            .select_from(MailTestDelivery)
            .where(
                MailTestDelivery.user_id == actor,
                MailTestDelivery.created_at > now - timedelta(minutes=1),
            )
        )
        if count and count >= 5:
            raise AuthFailure(429, 42999, "测试过于频繁，请稍后重试")
        row = MailTestDelivery(
            id=payload.request_id,
            user_id=actor,
            profile_id=str(payload.profile_id),
            revision=payload.expected_revision,
            recipient=payload.recipient,
            status="queued",
            created_at=now,
        )
        session.add(row)
        session.add(
            TaskProgressEventRecord(
                resource="mail-test",
                resource_id=row.id,
                at=now,
                level="info",
                kind="stage",
                stage="queued",
                message="Test message queued for Worker delivery.",
                completed=None,
                total=None,
                unit=None,
            )
        )
        session.add(
            SettingsAudit(
                user_id=actor,
                resource="mail",
                revision=payload.expected_revision,
                changes={
                    "action": "test_queued",
                    "request_id": str(row.id),
                    "profile_id": str(payload.profile_id),
                },
            )
        )
        await session.commit()
        return test_state(row)

    async def read(self, identity: UUID, actor: UUID) -> MailTestState:
        row = await self.settings.session.scalar(
            select(MailTestDelivery)
            .where(
                MailTestDelivery.id == identity,
                MailTestDelivery.user_id == actor,
            )
            .with_for_update()
        )
        if not row:
            raise AuthFailure(404, 40499, "测试请求不存在")
        now = datetime.now(UTC)
        if row.status == "sending" and row.lease_until and row.lease_until < now:
            row.status, row.error, row.finished_at = "unknown", "interrupted", now
        if row.status == "queued" and row.created_at < now - timedelta(minutes=10):
            row.status, row.error, row.finished_at = "failed", "queue_timeout", now
        await self.settings.session.commit()
        return test_state(row)
