"""Term administration, impact previews and live/public reference reconciliation."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from anyio import CapacityLimiter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.operations import AccountOperations
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.content.collaboration.models import CollaborationDocument
from app.modules.content.draft_models import ContentDraft
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import publish_taxonomies
from app.modules.content.management_schemas import (
    MetadataPatch,
    TaxonomyCatalog,
    TaxonomyCommand,
    TaxonomyImpact,
    TaxonomyItem,
    TaxonomyQuery,
    TaxonomyResult,
)
from app.modules.content.merge_lock import require_unlocked
from app.modules.content.metadata_edits import stage_metadata
from app.modules.content.models import Content, ContentTaxonomy, Taxonomy, TaxonomyName
from app.modules.content.publication import ContentPublication
from app.modules.content.taxonomy_names import TermDirectory, ensure_term
from app.modules.media.locking import lock_references
from app.modules.settings.models import SettingsAudit


class TaxonomyService:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session, self.config = session, config

    async def catalog(self, query: TaxonomyQuery) -> TaxonomyCatalog:
        directory = await TermDirectory.load(self.session)
        drafts = list(await self.session.scalars(select(ContentDraft)))
        counts: dict[UUID, int] = {}
        for draft in drafts:
            if draft.deleted_at:
                continue
            settings = directory.normalize(EditorSettings.model_validate(draft.editor_settings))
            for kind, names in (("category", settings.categories), ("tag", settings.tags)):
                for name in names:
                    identity = directory.names.get((kind, name.casefold()))
                    if identity:
                        counts[identity] = counts.get(identity, 0) + 1
        published = dict(
            (
                await self.session.execute(
                    select(ContentTaxonomy.taxonomy_id, func.count())
                    .join(Content, Content.id == ContentTaxonomy.content_id)
                    .where(Content.publication_status == "published")
                    .group_by(ContentTaxonomy.taxonomy_id)
                )
            ).all()
        )

        def item(row: Taxonomy) -> TaxonomyItem:
            return TaxonomyItem(
                id=row.id,
                kind=cast(Literal["category", "tag"], row.kind),
                name=row.name,
                slug=row.slug,
                parent_id=row.parent_id,
                revision=row.revision,
                draft_count=counts.get(row.id, 0),
                published_count=published.get(row.id, 0),
            )

        available = sorted(
            [row for row in directory.terms.values() if not row.retired],
            key=lambda row: (row.name.casefold(), str(row.id)),
        )
        filtered = [
            row
            for row in available
            if row.kind == query.kind and query.search.casefold().strip() in row.name.casefold()
        ]
        start = (query.page - 1) * query.page_size
        return TaxonomyCatalog(
            items=[item(row) for row in filtered[start : start + query.page_size]],
            parents=[item(row) for row in available if row.kind == query.kind],
            total=len(filtered),
            page=query.page,
            page_size=query.page_size,
        )

    async def existing(self, payload: TaxonomyCommand) -> tuple[TermDirectory, Taxonomy | None]:
        directory = await TermDirectory.load(self.session)
        row = directory.terms.get(payload.id) if payload.id else None
        if payload.action != "create" and (not row or row.retired or row.kind != payload.kind):
            raise AuthFailure(404, 40490, "分类或标签不存在")
        if row and row.revision != payload.expected_revision:
            raise AuthFailure(409, 40990, "分类或标签已修改，请刷新后重试")
        if payload.action in {"create", "update"}:
            key = (payload.kind, payload.name.casefold())
            if key in directory.names and (not row or directory.names[key] != row.id):
                raise AuthFailure(409, 40990, "此名称已被使用或保留，请使用其他名称")
        parent_id = payload.target_id if payload.action == "merge" else payload.parent_id
        parent = directory.terms.get(parent_id) if parent_id else None
        if parent_id and (not parent or parent.retired or parent.kind != payload.kind):
            raise AuthFailure(422, 42290, "目标分类或父分类不可用")
        if (
            payload.action == "merge"
            and parent
            and parent.revision != payload.expected_target_revision
        ):
            raise AuthFailure(409, 40990, "合并目标已修改，请刷新后重试")
        visited = {row.id} if row else set()
        while parent:
            if parent.id in visited:
                raise AuthFailure(422, 42290, "分类层级不能形成循环")
            visited.add(parent.id)
            parent = directory.terms.get(parent.parent_id) if parent.parent_id else None
        return directory, row

    async def references(
        self, directory: TermDirectory, row: Taxonomy | None
    ) -> tuple[list[ContentDraft], list[Content], list[Taxonomy]]:
        if row is None:
            return [], [], []
        field = "categories" if row.kind == "category" else "tags"

        def references(value: dict[str, object]) -> bool:
            settings = EditorSettings.model_validate(value)
            return row.name in directory.names_for(row.kind, getattr(settings, field))

        drafts = [
            item
            for item in await self.session.scalars(select(ContentDraft).order_by(ContentDraft.id))
            if references(item.editor_settings)
        ]
        linked = select(ContentTaxonomy.content_id).where(ContentTaxonomy.taxonomy_id == row.id)
        public = list(
            await self.session.scalars(
                select(Content).where(Content.id.in_(linked)).order_by(Content.id)
            )
        )
        children = [
            item
            for item in directory.terms.values()
            if not item.retired and item.parent_id == row.id
        ]
        return drafts, public, children

    async def preview(self, payload: TaxonomyCommand) -> TaxonomyImpact:
        await lock_references(self.session)
        directory, row = await self.existing(payload)
        drafts, public, children = await self.references(directory, row)
        sequences = dict(
            (
                await self.session.execute(
                    select(
                        CollaborationDocument.draft_id, CollaborationDocument.committed_seq
                    ).where(CollaborationDocument.draft_id.in_([item.id for item in drafts]))
                )
            ).all()
        )
        states = {
            "command": payload.model_dump(mode="json", exclude={"operation_id", "impact_token"}),
            "terms": [
                (str(term.id), term.revision, term.name, str(term.parent_id), term.retired)
                for term in sorted(directory.terms.values(), key=lambda value: str(value.id))
            ],
            "names": sorted(
                (kind, name, str(identity)) for (kind, name), identity in directory.names.items()
            ),
            "drafts": [
                (
                    str(item.id),
                    item.revision,
                    sequences.get(item.id),
                    item.updated_at.isoformat(),
                    item.scheduled_payload,
                )
                for item in drafts
            ],
            "public": [(str(item.id), item.public_generation) for item in public],
        }
        token = hashlib.sha256(
            json.dumps(states, ensure_ascii=True, sort_keys=True).encode()
        ).hexdigest()
        return TaxonomyImpact(
            token=token,
            drafts=len(drafts),
            published=sum(item.publication_status == "published" for item in public),
            scheduled=sum(item.scheduled_for is not None for item in drafts),
            children=len(children),
        )

    async def mutate(self, actor: Actor, payload: TaxonomyCommand) -> TaxonomyResult:
        await lock_references(self.session)
        operations = AccountOperations(self.session, CapacityLimiter(1))
        remembered = await operations.read(payload.operation_id, actor.id, "taxonomy", payload)
        if remembered:
            await self.session.commit()
            return TaxonomyResult.model_validate(remembered)
        impact = await self.preview(payload)
        if payload.action != "create" and payload.impact_token != impact.token:
            raise AuthFailure(409, 40990, "影响范围已变化，请重新查看并确认；未修改任何内容")
        directory, row = await self.existing(payload)
        drafts, public, children = await self.references(directory, row)
        for draft in drafts:
            await self.session.refresh(draft, with_for_update=True)
            await require_unlocked(self.session, draft.id)
        now = datetime.now(UTC)
        if row is None:
            row = await ensure_term(self.session, payload.kind, payload.name)
            assert row is not None
            row.parent_id = payload.parent_id
        else:
            for key, identity in directory.names.items():
                if identity != row.id:
                    continue
                alias = await self.session.get(TaxonomyName, key)
                target = (
                    payload.target_id
                    if payload.action == "merge"
                    else None
                    if payload.action == "remove"
                    else row.id
                )
                if alias:
                    alias.taxonomy_id = target
                else:
                    self.session.add(TaxonomyName(kind=key[0], name_key=key[1], taxonomy_id=target))
            if payload.action == "update":
                row.name, row.parent_id = payload.name, payload.parent_id
                key = (row.kind, row.name.casefold())
                if await self.session.get(TaxonomyName, key) is None:
                    self.session.add(TaxonomyName(kind=key[0], name_key=key[1], taxonomy_id=row.id))
            else:
                row.retired = True
                if payload.action == "merge":
                    assert payload.target_id is not None
                    directory.terms[payload.target_id].revision += 1
                for child in children:
                    child.parent_id = (
                        payload.target_id if payload.action == "merge" else row.parent_id
                    )
                    child.revision += 1
            row.revision += 1
        await self.session.flush()
        current = await TermDirectory.load(self.session)
        for draft in drafts:
            await stage_metadata(
                self.session, draft, actor.id, MetadataPatch(), current, note="分类与标签调整"
            )
        for content in public:
            settings = current.normalize(EditorSettings.model_validate(content.editor_settings))
            content.editor_settings = settings.model_dump(mode="json")
            content.public_generation += 1
            content.updated_at = now
            await publish_taxonomies(self.session, content.id, settings)
        if public:
            await ContentPublication(self.session, self.config).stage()
        self.session.add(
            SettingsAudit(
                user_id=actor.id,
                resource="taxonomies",
                revision=row.revision,
                changes={
                    "action": payload.action,
                    "id": str(row.id),
                    "target_id": str(payload.target_id) if payload.target_id else None,
                    "impact": impact.model_dump(),
                },
            )
        )
        result = TaxonomyResult(id=row.id, action=payload.action, updated_at=now)
        await operations.remember(
            payload.operation_id, actor.id, "taxonomy", payload, result.model_dump(mode="json")
        )
        await self.session.commit()
        return result
