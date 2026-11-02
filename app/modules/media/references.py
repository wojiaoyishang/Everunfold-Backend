"""Conservative checks across current references and retained history."""

import uuid

from sqlalchemy import Text, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.models import CollaborationDocument
from app.modules.content.draft_models import ContentDraft, DraftRevision
from app.modules.content.models import Comment, Content, SiteSettings
from app.modules.media.models import MediaAsset
from app.modules.settings.models import SettingsAudit, SettingsDeliveryEvent


async def require_unreferenced(
    session: AsyncSession,
    asset: MediaAsset,
    *,
    history: bool,
    removing_ids: set[uuid.UUID] | None = None,
) -> None:
    # IDs are stable through Base URL changes. Checking serialized documents also
    # finds IDs embedded in URLs/HTML/Markdown; false positives intentionally block removal.
    token = str(asset.id)
    current = [
        select(CollaborationDocument.draft_id).where(
            cast(CollaborationDocument.media_ids, Text).contains(token)
        ),
        select(ContentDraft.id).where(
            or_(
                cast(ContentDraft.body, Text).contains(token),
                cast(ContentDraft.editor_settings, Text).contains(token),
            )
        ),
        select(SiteSettings.id).where(cast(SiteSettings.data, Text).contains(token)),
        select(Content.id).where(
            or_(
                cast(Content.content, Text).contains(token),
                cast(Content.media_urls, Text).contains(token),
                Content.cover_url.contains(token),
                cast(Content.editor_settings, Text).contains(token),
            )
        ),
        select(Comment.id).where(Comment.content.contains(token)),
    ]
    for statement in current:
        if await session.scalar(statement.limit(1)) is not None:
            raise AuthFailure(409, 40936, "文件仍被站点设置或内容使用，请先移除引用")
    if history:
        retained = [
            select(DraftRevision.draft_id).where(
                cast(DraftRevision.snapshot, Text).contains(token)
            ),
            select(SettingsDeliveryEvent.id).where(
                cast(SettingsDeliveryEvent.payload, Text).contains(token)
            ),
            select(SettingsAudit.id).where(
                SettingsAudit.resource == "public-settings",
                cast(SettingsAudit.changes, Text).contains(token),
            ),
            select(MediaAsset.id).where(
                MediaAsset.source_id == asset.id,
                MediaAsset.id.not_in(removing_ids or set()),
            ),
        ]
        for statement in retained:
            if await session.scalar(statement.limit(1)) is not None:
                raise AuthFailure(
                    409, 40937, "历史设置或编辑副本仍引用此原件，可以放入回收站，但暂不能删除原文件"
                )
