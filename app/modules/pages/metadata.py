"""Private page metadata is explicitly requested and never part of page releases."""

from uuid import UUID

from app.modules.auth.accounts_service import AccountsService
from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure
from app.modules.pages.models import ManagedPage
from app.modules.pages.schemas import PageMetadata, SavePageMetadata
from app.modules.settings.models import SettingsAudit


class PageMetadataService:
    def __init__(self, accounts: AccountsService) -> None:
        self.accounts, self.session = accounts, accounts.session

    async def target(self, identity: UUID) -> ManagedPage:
        row = await self.session.get(ManagedPage, identity, populate_existing=True)
        if row is None:
            raise AuthFailure(404, 40496, "页面不存在")
        return row

    @staticmethod
    def project(row: ManagedPage) -> PageMetadata:
        return PageMetadata(
            page_id=row.id, revision=row.internal_revision, value=row.internal_metadata
        )

    async def read(self, identity: UUID) -> PageMetadata:
        return self.project(await self.target(identity))

    async def save(self, actor: User, identity: UUID, payload: SavePageMetadata) -> PageMetadata:
        fingerprint = {"page_id": str(identity), **payload.model_dump(mode="json")}
        cached = await self.accounts.operations.read(
            payload.operation_id, actor.id, "page_metadata", fingerprint
        )
        if cached is not None:
            return PageMetadata.model_validate(cached)
        row = await self.target(identity)
        if row.deleted_at is not None:
            raise AuthFailure(409, 40996, "请先恢复回收站中的页面")
        if row.internal_revision != payload.expected_revision:
            raise AuthFailure(409, 40999, "页面内部元数据已被修改，请读取后重新合并")
        row.internal_metadata = payload.value
        row.internal_revision += 1
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="page-metadata:" + str(identity),
                revision=row.internal_revision,
                changes={"action": "metadata"},
            )
        )
        result = self.project(row)
        await self.accounts.operations.remember(
            payload.operation_id,
            actor.id,
            "page_metadata",
            fingerprint,
            result.model_dump(mode="json"),
            sensitive=True,
        )
        await self.session.commit()
        return result
