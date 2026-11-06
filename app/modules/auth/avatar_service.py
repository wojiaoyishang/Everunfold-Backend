"""Owned avatar reservations bound quota races; immutable originals use the shared publisher."""

import asyncio
import hashlib
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select

from app.core.config import Settings
from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.avatar_models import AvatarPolicy, UserAvatar
from app.modules.auth.avatar_schemas import (
    AvatarCatalog,
    AvatarItem,
    AvatarPolicySave,
    AvatarPolicyState,
    AvatarUpload,
)
from app.modules.auth.models import AuthAuditEvent, User
from app.modules.auth.operations import AccountOperations
from app.modules.auth.service import AuthFailure
from app.modules.media.locking import lock_references, media_operation
from app.modules.media.models import MediaAsset
from app.modules.media.service import MediaService


def normalize_avatar(data: bytes, edge: int) -> bytes:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
                raise ValueError("unsupported avatar format")
            if source.width * source.height > 16777216:
                raise ValueError("avatar dimensions too large")
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            image.info.clear()
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=90, method=4)
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise AuthFailure(
            422, 42265, "请选择静态 JPG、PNG 或 WebP 图片，像素总量不超过1600万"
        ) from error


def policy_state(row: AvatarPolicy) -> AvatarPolicyState:
    return AvatarPolicyState.model_validate(row, from_attributes=True)


def avatar_item(
    row: UserAvatar, asset: MediaAsset | None, config: Settings, reused: bool = False
) -> AvatarItem:
    available = (
        asset is not None
        and asset.status == "ready"
        and asset.deleted_at is None
        and not asset.purge_pending
    )
    return AvatarItem(
        id=row.id,
        filename=row.filename,
        created_at=row.created_at,
        status="ready" if available else "pending" if row.asset_id is None else "unavailable",
        url=config.media_base_url + "/" + asset.object_key if available and asset else None,
        reused=reused,
    )


class AvatarService:
    def __init__(self, accounts: AccountsService, config: Settings) -> None:
        self.accounts, self.config, self.session = accounts, config, accounts.session

    async def policy(self) -> AvatarPolicyState:
        row = await self.session.get(AvatarPolicy, 1)
        if row is None:
            raise AuthFailure(503, 50365, "头像上传设置尚未就绪")
        return policy_state(row)

    async def save_policy(self, actor: User, payload: AvatarPolicySave) -> AvatarPolicyState:
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "avatar_policy", payload
        )
        if cached is not None:
            return AvatarPolicyState.model_validate(cached)
        await self.accounts.confirm_password(actor, payload.current_password)
        row = await self.session.get(AvatarPolicy, 1, with_for_update=True)
        assert row is not None
        if row.revision != payload.expected_revision:
            raise AuthFailure(409, 40965, "头像上传限制已被修改，请重新读取")
        for name, value in payload.value.model_dump().items():
            setattr(row, name, value)
        row.revision += 1
        self.accounts.audit(
            actor, "avatar_policy_updated", actor, policy=payload.value.model_dump()
        )
        result = policy_state(row)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            "avatar_policy",
            payload,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def catalog(self, actor: User) -> AvatarCatalog:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        records = await self.session.execute(
            select(UserAvatar, MediaAsset)
            .outerjoin(MediaAsset, MediaAsset.id == UserAvatar.asset_id)
            .where(UserAvatar.user_id == actor.id, UserAvatar.removed_at.is_(None))
            .order_by(UserAvatar.created_at.desc(), UserAvatar.id)
        )
        rows = [avatar_item(row, asset, self.config) for row, asset in records]
        today = await self.session.scalar(
            select(func.count())
            .select_from(UserAvatar)
            .where(UserAvatar.user_id == actor.id, UserAvatar.created_at >= start)
        )
        return AvatarCatalog(
            items=rows,
            policy=await self.policy(),
            uploaded_today=today or 0,
            retained=len(rows),
            resets_at=start + timedelta(days=1),
        )

    async def upload(self, actor: User, metadata: AvatarUpload, data: bytes) -> AvatarItem:
        if actor.must_change_password:
            raise AuthFailure(403, 40363, "请先在账号中心修改初始密码")
        limits = await self.policy()
        if len(data) > limits.max_bytes:
            raise AuthFailure(413, 41365, "头像文件超过当前上传限制")
        allowed = await self.accounts.auth.repo.consume_limit(
            "avatar-upload:" + str(actor.id),
            max(20, limits.daily_uploads * 3),
            3600,
            datetime.now(UTC),
        )
        await self.session.commit()
        if not allowed:
            raise AuthFailure(429, 42901, "尝试次数较多，请稍后再试")
        normalized = await asyncio.to_thread(normalize_avatar, data, limits.max_edge)
        identity = uuid5(actor.id, str(metadata.upload_id))
        signature = hashlib.sha256(metadata.filename.encode() + b"\0" + data).hexdigest()
        async with media_operation(self.session, f"avatar-user:{actor.id}") as work:
            row = await work.get(UserAvatar, identity)
            if row is not None:
                if row.signature != signature:
                    raise AuthFailure(409, 40930, "该上传标识已经用于其他文件")
                if row.removed_at is not None:
                    raise AuthFailure(410, 41065, "头像已从个人图库移除，请重新上传")
                asset = await work.get(MediaAsset, row.asset_id) if row.asset_id else None
                if asset:
                    return avatar_item(row, asset, self.config, reused=True)
            else:
                owner = await work.get(User, actor.id)
                if owner is None or not owner.active or owner.auth_version != actor.auth_version:
                    raise AuthFailure(401, 40101, "请先登录")
                policy = await work.get(AvatarPolicy, 1)
                assert policy is not None
                now = datetime.now(UTC)
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                count = await work.scalar(
                    select(func.count())
                    .select_from(UserAvatar)
                    .where(UserAvatar.user_id == actor.id, UserAvatar.removed_at.is_(None))
                )
                today = await work.scalar(
                    select(func.count())
                    .select_from(UserAvatar)
                    .where(UserAvatar.user_id == actor.id, UserAvatar.created_at >= start)
                )
                if (count or 0) >= policy.retained_images:
                    raise AuthFailure(409, 40966, "个人头像图库已满，请先移除不再需要的图片")
                if (today or 0) >= policy.daily_uploads:
                    raise AuthFailure(429, 42962, "今日头像上传次数已用完，请明天再试")
                if len(data) > policy.max_bytes or limits.max_edge > policy.max_edge:
                    raise AuthFailure(409, 40965, "头像上传限制已被修改，请重新读取")
                row = UserAvatar(
                    id=identity,
                    user_id=actor.id,
                    filename=metadata.filename,
                    signature=signature,
                    created_at=now,
                )
                work.add(row)
            # A failed storage operation retains a durable reservation. Same upload_id retries it.
            await work.commit()
            result = await MediaService(self.session, self.config).publish(
                normalized,
                "image/webp",
                "webp",
                "avatar-" + metadata.upload_id.hex[:12] + ".webp",
                str(metadata.upload_id),
                actor.id,
                "avatar",
            )
            await lock_references(work)
            asset = await work.get(
                MediaAsset, UUID(result.id.removeprefix("media:")), populate_existing=True
            )
            if (
                asset is None
                or asset.status != "ready"
                or asset.deleted_at is not None
                or asset.purge_pending
            ):
                raise AuthFailure(409, 40965, "头像资源暂不可用，请重新上传")
            row.asset_id = asset.id
            work.add(
                AuthAuditEvent(
                    user_id=actor.id,
                    target_user_id=actor.id,
                    action="avatar_uploaded",
                    created_at=datetime.now(UTC),
                    details={"avatar_id": str(row.id)},
                )
            )
            await work.commit()
            return avatar_item(row, asset, self.config, reused=result.reused)

    async def remove(
        self, actor: User, avatar_id: UUID, operation_id: UUID, *, owner: User | None = None
    ) -> None:
        owner = owner or actor
        payload = {"owner_id": str(owner.id), "avatar_id": str(avatar_id)}
        async with media_operation(self.session, f"avatar-user:{owner.id}") as work:
            operations = AccountOperations(work, self.accounts.auth.password_limiter)
            if await operations.read(operation_id, actor.id, "avatar_remove", payload) is not None:
                return
            row = await work.get(UserAvatar, avatar_id)
            if row is None or row.user_id != owner.id:
                raise AuthFailure(404, 40465, "头像不存在")
            current = await work.get(User, owner.id, with_for_update=True)
            if current and current.avatar_id == row.id:
                raise AuthFailure(409, 40967, "请先更换或清除当前头像，再从图库移除")
            await lock_references(work)
            row.removed_at = row.removed_at or datetime.now(UTC)
            work.add(
                AuthAuditEvent(
                    user_id=actor.id,
                    target_user_id=owner.id,
                    action="avatar_removed",
                    created_at=datetime.now(UTC),
                    details={"avatar_id": str(row.id)},
                )
            )
            await operations.remember(
                operation_id, actor.id, "avatar_remove", payload, {"done": True}
            )
            await work.commit()
