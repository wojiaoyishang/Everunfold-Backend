"""Audited manual email decisions, distinct from proof of mailbox control."""

from datetime import UTC, datetime
from uuid import UUID

from app.modules.auth.accounts_schemas import AccountProfile, ReviewAccountEmail
from app.modules.auth.accounts_service import AccountsService, profile
from app.modules.auth.email_verification_service import invalidate_email_requests
from app.modules.auth.models import User
from app.modules.auth.permissions import user_permissions
from app.modules.auth.service import AuthFailure


async def review_email(
    service: AccountsService, actor: User, user_id: UUID, payload: ReviewAccountEmail
) -> AccountProfile:
    if user_permissions(actor).get("users:verify_email") != "all":
        raise AuthFailure(403, 40374, "没有审核邮箱验证的权限")
    envelope = {"user_id": str(user_id), **payload.model_dump(mode="json")}
    cached = await service.operations.read(payload.operation_id, actor.id, "email_review", envelope)
    if cached is not None:
        return AccountProfile.model_validate(cached)
    user = await service.target(user_id, lock=True)
    if user.role == "admin" and actor.role != "admin":
        raise AuthFailure(403, 40374, "不能审核管理员账号的邮箱")
    service.version(user, payload.expected_revision)
    if not user.email:
        raise AuthFailure(422, 42274, "请先保存用户邮箱")
    now = datetime.now(UTC)
    if payload.action == "approve":
        if user.email_verified_at:
            raise AuthFailure(409, 40976, "邮箱已通过本人验证")
        user.email_reviewed_at = now
    else:
        user.email_verified_at = None
        user.email_reviewed_at = None
        await invalidate_email_requests(service.session, user_id, "review_revoked")
    user.revision += 1
    user.updated_at = now
    service.audit(actor, "email_review_" + payload.action, user, reason=payload.reason)
    result = profile(user)
    await service.operations.remember(
        payload.operation_id, actor.id, "email_review", envelope, result.model_dump(mode="json")
    )
    await service.session.commit()
    return result
