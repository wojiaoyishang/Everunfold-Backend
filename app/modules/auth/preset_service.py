"""Durable permission templates; applying one copies validated grants into a user."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.models import User
from app.modules.auth.permissions import ResourceScope
from app.modules.auth.preset_models import UserPermissionPreset
from app.modules.auth.preset_schemas import (
    UserPreset,
    UserPresetCatalog,
    UserPresetCreate,
    UserPresetRemove,
    UserPresetSave,
    UserPresetSelection,
    UserPresetStamp,
)
from app.modules.auth.service import AuthFailure


def preset_value(row: UserPermissionPreset) -> UserPreset:
    return UserPreset(
        id=row.id,
        name=row.name,
        description=row.description,
        grants=cast(dict[str, dict[str, ResourceScope]], row.grants),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def preset_target(
    session: AsyncSession, identity: UUID, expected: int
) -> UserPermissionPreset:
    row = await session.get(UserPermissionPreset, identity, populate_existing=True)
    if row is None or row.deleted_at is not None:
        raise AuthFailure(404, 40470, "身份预设不存在或已移除")
    if row.revision != expected:
        raise AuthFailure(409, 40970, "身份预设已被修改，请重新读取后再操作")
    return row


async def resolve_preset(
    session: AsyncSession,
    choice: UserPresetSelection,
    role: str,
) -> tuple[dict[str, dict[str, ResourceScope]], dict[str, Any]]:
    if role != "user":
        raise AuthFailure(422, 42270, "管理员不能应用成员身份预设")
    row = await preset_target(session, choice.id, choice.expected_revision)
    grants = {app: dict(items) for app, items in row.grants.items()}
    stamp = UserPresetStamp(id=row.id, name=row.name, revision=row.revision)
    return cast(dict[str, dict[str, ResourceScope]], grants), stamp.model_dump(mode="json")


class UserPresetsService:
    def __init__(self, accounts: AccountsService) -> None:
        self.accounts = accounts
        self.session = accounts.session

    @staticmethod
    def require_admin(actor: User) -> None:
        if actor.role != "admin":
            raise AuthFailure(403, 40370, "仅管理员可以管理身份预设")

    async def catalog(self, actor: User) -> UserPresetCatalog:
        rows = await self.session.scalars(
            select(UserPermissionPreset)
            .where(UserPermissionPreset.deleted_at.is_(None))
            .order_by(UserPermissionPreset.created_at, UserPermissionPreset.id)
        )
        items = []
        for row in rows:
            value = preset_value(row)
            if actor.role != "admin":
                try:
                    await self.accounts.validate_grants(actor, value.grants, "user")
                except AuthFailure as failure:
                    if failure.status not in {403, 422}:
                        raise
                    continue
            items.append(value)
        return UserPresetCatalog(items=items, can_manage=actor.role == "admin")

    async def unique_name(self, name: str, identity: UUID) -> None:
        other = await self.session.scalar(
            select(UserPermissionPreset.id).where(
                UserPermissionPreset.name_key == name.casefold(),
                UserPermissionPreset.deleted_at.is_(None),
                UserPermissionPreset.id != identity,
            )
        )
        if other is not None:
            raise AuthFailure(409, 40971, "身份预设名称已存在")

    async def save(
        self, actor: User, payload: UserPresetCreate, identity: UUID | None = None
    ) -> UserPreset:
        self.require_admin(actor)
        kind = "preset_update" if identity else "preset_create"
        envelope = {
            "preset_id": str(identity) if identity else None,
            **payload.model_dump(mode="json"),
        }
        cached = await self.accounts.operations.read(payload.operation_id, actor.id, kind, envelope)
        if cached is not None:
            return UserPreset.model_validate(cached)
        await self.accounts.confirm_password(actor, payload.current_password)
        if identity:
            assert isinstance(payload, UserPresetSave)
            row = await preset_target(self.session, identity, payload.expected_revision)
            row.revision += 1
        else:
            now = datetime.now(UTC)
            row = UserPermissionPreset(id=uuid4(), revision=1, created_at=now, updated_at=now)
        await self.accounts.validate_grants(actor, payload.value.grants, "user")
        await self.unique_name(payload.value.name, row.id)
        row.name, row.name_key = payload.value.name, payload.value.name.casefold()
        row.description = payload.value.description
        row.grants = {app: dict(items) for app, items in payload.value.grants.items()}
        row.updated_at = datetime.now(UTC)
        self.session.add(row)
        self.accounts.audit(actor, kind, actor, preset_id=str(row.id), revision=row.revision)
        result = preset_value(row)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            kind,
            envelope,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result

    async def remove(
        self, actor: User, identity: UUID, payload: UserPresetRemove
    ) -> UserPresetCatalog:
        self.require_admin(actor)
        envelope = {"preset_id": str(identity), **payload.model_dump(mode="json")}
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "preset_remove", envelope
        )
        if cached is None:
            await self.accounts.confirm_password(actor, payload.current_password)
            row = await preset_target(self.session, identity, payload.expected_revision)
            row.deleted_at = row.updated_at = datetime.now(UTC)
            row.revision += 1
            self.accounts.audit(actor, "preset_remove", actor, preset_id=str(row.id))
            await self.accounts.operations.remember(
                payload.operation_id,
                actor.id,
                "preset_remove",
                envelope,
                {"removed": True},
                sensitive=True,
            )
            await self.session.commit()
        return await self.catalog(actor)
