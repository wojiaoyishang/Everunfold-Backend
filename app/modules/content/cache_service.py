"""Maintain a content projection while keeping complete replica releases immutable."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH

from app.modules.auth.service import AuthFailure
from app.modules.content.cache_schemas import (
    ContentCacheCommand,
    ContentCacheNode,
    ContentCacheState,
    ContentCacheStatus,
    ContentCacheTarget,
)
from app.modules.content.draft_models import ContentDraft
from app.modules.content.models import SiteSettings
from app.modules.content.publication import ContentPublication
from app.modules.content.publication_models import ContentRelease
from app.modules.media.locking import lock_references
from app.modules.pages.models import ManagedPage
from app.modules.settings.cache_event_schema import CacheChange
from app.modules.settings.models import SettingsAudit
from app.modules.settings.node_registry import read_directory
from app.modules.settings.nodes_schemas import NodeStatus, ReceiverProbe
from app.modules.settings.service import SettingsService
from app.modules.settings.sync_status import read_history

type Projection = tuple[dict[str, Any] | None, list[dict[str, Any]] | None]


class ContentCache:
    def __init__(self, service: SettingsService, target: ContentCacheTarget) -> None:
        self.service, self.target = service, target
        self.session = service.session
        self.audit_resource = f"content-cache:{target.kind}:{target.id}"

    async def _ensure_content(self) -> None:
        if self.target.kind == "page":
            statement = select(ManagedPage.id).where(ManagedPage.id == self.target.id)
        else:
            statement = select(ContentDraft.id).where(
                ContentDraft.id == self.target.id, ContentDraft.type_id == self.target.kind
            )
        if await self.session.scalar(statement) is None:
            raise AuthFailure(404, 40454, "内容不存在")

    async def _projections(self, site: SiteSettings, revisions: set[int]) -> dict[int, Projection]:
        snapshot = ContentRelease.snapshot
        identity = str(self.target.id)
        item = func.jsonb_path_query_first(
            snapshot["pages" if self.target.kind == "page" else "contents"],
            cast("$[*] ? (@.id == $id)", JSONPATH),
            func.jsonb_build_object("id", cast(identity, Text)),
            type_=JSONB,
        )
        rows = await self.session.execute(
            select(ContentRelease.revision, item, snapshot["comments"][identity]).where(
                ContentRelease.source_epoch == site.source_epoch,
                ContentRelease.revision.in_(revisions),
            )
        )
        return {revision: (entry, comments) for revision, entry, comments in rows}

    async def _inspect(self, site: SiteSettings) -> tuple[ContentCacheState, tuple[str, ...]]:
        directory = await read_directory(self.session, self.service.config)
        targets = directory.targets
        nodes = [
            NodeStatus(
                id=node,
                enabled=entry.enabled,
                base_url=entry.base_url,
                receiver_configured=bool(
                    (binding := targets.get(node)) and binding.settings_receiver_url
                ),
                receiver=ReceiverProbe(state="unavailable"),
            )
            for node, entry in directory.entries.items()
        ]
        await read_history(
            self.session, nodes, datetime.now(UTC), self.service.config.outbox.max_attempts
        )
        history = [node.sync["public-content"] for node in nodes]
        revisions = {site.content_generation} | {
            row.acknowledged for row in history if row.acknowledged is not None
        }
        projections = await self._projections(site, revisions)
        wanted = projections.get(site.content_generation, (None, None))
        manual_floor = await self.session.scalar(
            select(func.max(SettingsAudit.revision)).where(
                SettingsAudit.resource == self.audit_resource,
                SettingsAudit.changes["source_epoch"].astext == str(site.source_epoch),
            )
        )
        paths = {wanted[0]["path"]} if wanted[0] is not None else set()
        states: list[ContentCacheNode] = []
        for node, row in zip(nodes, history, strict=True):
            actual = projections.get(row.acknowledged) if row.acknowledged is not None else None
            if actual and actual[0] is not None:
                paths.add(actual[0]["path"])
            state: ContentCacheStatus = row.state
            if state not in {"disabled", "not_configured"}:
                if wanted[0] is None and (actual is None or actual[0] is None) and not manual_floor:
                    state = "not_published"
                elif (
                    actual is not None
                    and actual == wanted
                    and (not manual_floor or (row.acknowledged or 0) >= manual_floor)
                ):
                    state = "applied"
                elif state == "applied":
                    state = "unqueued"
            states.append(
                ContentCacheNode(
                    node_id=node.id,
                    state=state,
                    acknowledged_revision=row.acknowledged,
                    acknowledged_at=row.acknowledged_at,
                    error=row.error if state in {"retrying", "failed"} else None,
                )
            )
        return (
            ContentCacheState(
                published=wanted[0] is not None,
                manageable=bool(paths),
                nodes=states,
            ),
            tuple(sorted(paths)),
        )

    async def state(self) -> ContentCacheState:
        await self._ensure_content()
        # Both reads of the site generation and the receipt comparison use the same head.
        site = await self.session.scalar(
            select(SiteSettings).where(SiteSettings.id == 1).with_for_update(read=True)
        )
        if site is None:
            raise AuthFailure(503, 50320, "站点设置尚未初始化")
        return (await self._inspect(site))[0]

    async def run(self, command: ContentCacheCommand, actor_id: UUID) -> ContentCacheState:
        await lock_references(self.session)
        await self._ensure_content()
        site = await self.service.site(locked=True)
        state, paths = await self._inspect(site)
        if not state.manageable:
            raise AuthFailure(409, 40925, "此内容暂无可维护的公开副本")
        publication = ContentPublication(self.session, self.service.config)
        if command.action == "clear":
            await publication.stage(CacheChange("projection_changed", self.target.id, paths))
        else:
            # A retry uses the published projection and existing durable delivery protocol.
            await publication.stage_retry(None)
        self.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource=self.audit_resource,
                revision=site.content_generation,
                changes={"action": command.action, "source_epoch": str(site.source_epoch)},
            )
        )
        await self.session.commit()
        return await self.state()
