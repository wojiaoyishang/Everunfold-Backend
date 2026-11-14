"""Portable projections of persisted records and their resource-specific input validation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import PublicModel
from app.modules.auth.service import AuthFailure
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.draft_schemas import DraftFields
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.models import Comment, Content, SiteSettings, Taxonomy
from app.modules.pages.groups import source_value
from app.modules.pages.models import ManagedPage, PageTemplate, PageTranslationGroup
from app.modules.pages.schemas import PageValue, TitledPageCode
from app.modules.settings.defaults import public_settings
from app.modules.settings.models import SettingsAudit
from app.plugins.glossary.models import GlossaryTerm
from app.plugins.glossary.schemas import GlossaryFields, GlossarySeo
from app.plugins.glossary.service import row_feature_media_id

from .schemas import (
    MAX_BUNDLE_BYTES,
    MAX_ITEMS,
    MaintenanceBundleItem,
    MaintenanceExportRequest,
    MaintenanceKind,
    MaintenanceStatus,
)

UNDATED = datetime(1970, 1, 1, tzinfo=UTC)
NAMESPACE = UUID("9f710162-f607-4ee1-8519-8eac88fa3f69")
SITE_ID = uuid5(NAMESPACE, "site")


class DraftRecord(PublicModel):
    language: str = Field(min_length=1, max_length=32)
    group_id: UUID
    source_id: UUID
    fields: DraftFields

    @model_validator(mode="after")
    def reject_password(self) -> DraftRecord:
        if self.fields.access_password is not None:
            raise ValueError("Portable drafts do not carry access passwords")
        return self


class PageRecord(PublicModel):
    type_id: str = Field(min_length=1, max_length=100)
    language: str | None = None
    group_id: UUID | None = None
    source_id: UUID | None = None
    template_id: UUID | None = None
    value: PageValue


class TaxonomyRecord(PublicModel):
    name: str
    slug: str = Field(pattern=r"^[\w-]{1,180}$")
    parent_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return EditorSettings(categories=[value]).categories[0]


class CommentRecord(PublicModel):
    target_kind: Literal["content", "page"]
    target_id: UUID
    parent_id: UUID | None = None
    author: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    body_format: Literal["plain", "markdown"] = "plain"
    website: str | None = Field(default=None, max_length=2048)
    created_at: AwareDatetime


def status_of_comment(row: Comment) -> MaintenanceStatus:
    if row.status == "approved":
        return "published"
    return "draft" if row.status == "pending" else "archived"


async def draft_record(session: AsyncSession, row: ContentDraft) -> DraftRecord:
    group = await session.get(TranslationGroup, row.translation_group_id)
    return DraftRecord(
        language=row.language,
        group_id=row.translation_group_id,
        source_id=group.source_id if group else row.id,
        fields=DraftFields.model_validate(
            {
                "title": row.title,
                "excerpt": row.excerpt,
                "body": row.body,
                "edit_mode": row.edit_mode,
                "component_document": row.component_document,
                "settings": row.editor_settings,
            }
        ),
    )


async def page_record(session: AsyncSession, row: ManagedPage) -> PageRecord:
    group = (
        await session.get(PageTranslationGroup, row.translation_group_id)
        if row.translation_group_id
        else None
    )
    return PageRecord(
        type_id=row.type_id,
        template_id=row.template_id,
        language=row.language,
        group_id=row.translation_group_id,
        source_id=group.source_id if group else None,
        value=source_value(row.data),
    )


def comment_record(row: Comment) -> CommentRecord:
    target_id = row.page_id or row.content_id
    assert target_id is not None
    return CommentRecord(
        target_kind="page" if row.page_id else "content",
        target_id=target_id,
        parent_id=row.parent_id,
        author=row.author,
        body=row.content,
        body_format=getattr(row, "body_format", "plain"),
        website=getattr(row, "website", None),
        created_at=row.created_at,
    )


def glossary_record(row: GlossaryTerm) -> GlossaryFields:
    return GlossaryFields.model_validate(
        {
            **GlossarySeo.model_validate(row.seo).model_dump(),
            "language": row.language,
            "slug": row.slug,
            "title": row.title,
            "summary": row.summary,
            "feature_media_id": row_feature_media_id(row),
            "show_image_viewer": row.show_image_viewer,
            "body": row.body,
            "aliases": row.aliases,
            "auto_match_title": row.auto_match_title,
            "match_rules": row.match_rules,
            "status": "draft",
        }
    )


def template_record(row: PageTemplate) -> TitledPageCode:
    return TitledPageCode(**row.code, title=row.title, description=row.description)


def taxonomy_record(row: Taxonomy) -> TaxonomyRecord:
    return TaxonomyRecord(name=row.name, slug=row.slug, parent_id=row.parent_id)


def _within(
    query: Any, request: MaintenanceExportRequest, status: Any, updated: Any, identity: Any
) -> Any:
    if request.statuses:
        query = query.where(status.in_(request.statuses))
    if request.date_from:
        query = query.where(updated >= request.date_from)
    if request.date_to:
        query = query.where(updated <= request.date_to)
    return (
        query.order_by(updated.desc(), identity)
        .limit(MAX_ITEMS + 1)
        .execution_options(yield_per=10)
    )


def _undated_included(request: MaintenanceExportRequest, status: MaintenanceStatus) -> bool:
    return not (request.date_from or request.date_to) and (
        not request.statuses or status in request.statuses
    )


def _item(
    kind: MaintenanceKind,
    row: Any,
    title: str,
    status: MaintenanceStatus,
    value: PublicModel,
    updated_at: datetime | None = None,
) -> MaintenanceBundleItem:
    return MaintenanceBundleItem(
        kind=kind,
        id=row.id,
        title=title[:1000],
        status=status,
        updated_at=updated_at or row.updated_at,
        value=value.model_dump(mode="json"),
    )


async def collect(
    session: AsyncSession, request: MaintenanceExportRequest
) -> list[MaintenanceBundleItem]:
    items: list[MaintenanceBundleItem] = []
    kinds = set(request.kinds)
    byte_count = 0

    def add(item: MaintenanceBundleItem) -> None:
        nonlocal byte_count
        byte_count += len(item.model_dump_json().encode()) + 1
        if byte_count > MAX_BUNDLE_BYTES:
            raise AuthFailure(413, 41381, "导出超过 16 MiB，请缩小范围。")
        items.append(item)
        if len(items) > MAX_ITEMS:
            raise AuthFailure(413, 41380, "导出超过 2000 条记录，请缩小范围。")

    if kinds & {"article", "shuoshuo"}:
        query = select(ContentDraft).where(
            ContentDraft.type_id.in_(kinds & {"article", "shuoshuo"})
        )
        status = case(
            (ContentDraft.deleted_at.is_not(None), "archived"),
            (ContentDraft.published_revision.is_not(None), "published"),
            else_="draft",
        )
        async for row in await session.stream_scalars(
            _within(query, request, status, ContentDraft.updated_at, ContentDraft.id)
        ):
            add(
                _item(
                    cast(MaintenanceKind, row.type_id),
                    row,
                    row.title,
                    "archived"
                    if row.deleted_at
                    else "published"
                    if row.published_revision
                    else "draft",
                    await draft_record(session, row),
                )
            )
        # Earlier public entries have no native working draft to project.
        legacy = select(Content).where(
            Content.kind.in_(kinds & {"article", "shuoshuo"}),
            ~select(ContentDraft.id).where(ContentDraft.id == Content.id).exists(),
        )
        states = case(
            (Content.publication_status == "withdrawn", "archived"),
            else_=Content.publication_status,
        )
        async for row in await session.stream_scalars(
            _within(legacy, request, states, Content.updated_at, Content.id)
        ):
            blocks = [
                {
                    "id": str(uuid5(row.id, str(index))),
                    "type": source["type"],
                    "content": source["content"],
                }
                for index, source in enumerate(row.content)
                if source.get("type") in {"markdown", "html"}
            ]
            if not blocks:
                blocks = [{"id": str(uuid5(row.id, "body")), "type": "markdown", "content": ""}]
            fields = DraftFields.model_validate(
                {
                    "title": row.title[:200],
                    "excerpt": row.excerpt[:1000] if row.kind == "article" else "",
                    "body": blocks,
                    "settings": {
                        "byline": row.author[:100],
                        "display_published_at": row.published_at,
                    },
                }
            )
            item = DraftRecord(
                language=row.language, group_id=row.id, source_id=row.id, fields=fields
            )
            status_value = (
                "archived" if row.publication_status == "withdrawn" else row.publication_status
            )
            add(
                _item(
                    cast(MaintenanceKind, row.kind),
                    row,
                    row.title,
                    cast(MaintenanceStatus, status_value),
                    item,
                )
            )
    if "page" in kinds:
        states = case(
            (ManagedPage.deleted_at.is_not(None), "archived"),
            (ManagedPage.published.is_not(None), "published"),
            else_="draft",
        )
        async for row in await session.stream_scalars(
            _within(select(ManagedPage), request, states, ManagedPage.updated_at, ManagedPage.id)
        ):
            record = await page_record(session, row)
            add(
                _item(
                    "page",
                    row,
                    record.value.title,
                    "archived"
                    if row.deleted_at
                    else "published"
                    if row.published is not None
                    else "draft",
                    record,
                )
            )
    if "glossary" in kinds:
        async for row in await session.stream_scalars(
            _within(
                select(GlossaryTerm),
                request,
                GlossaryTerm.status,
                GlossaryTerm.updated_at,
                GlossaryTerm.id,
            )
        ):
            add(
                _item(
                    "glossary",
                    row,
                    row.title,
                    cast(MaintenanceStatus, row.status),
                    glossary_record(row),
                )
            )
    if kinds & {"category", "tag"} and any(
        _undated_included(request, state) for state in ("published", "archived")
    ):
        taxonomy_query = select(Taxonomy).where(Taxonomy.kind.in_(kinds & {"category", "tag"}))
        if request.statuses and len(set(request.statuses) & {"published", "archived"}) == 1:
            taxonomy_query = taxonomy_query.where(
                Taxonomy.retired.is_("archived" in request.statuses)
            )
        async for taxonomy in await session.stream_scalars(
            taxonomy_query.order_by(Taxonomy.id)
            .limit(MAX_ITEMS + 1)
            .execution_options(yield_per=20)
        ):
            add(
                _item(
                    cast(MaintenanceKind, taxonomy.kind),
                    taxonomy,
                    taxonomy.name,
                    "archived" if taxonomy.retired else "published",
                    taxonomy_record(taxonomy),
                    UNDATED,
                )
            )
    if "comment" in kinds:
        states = case(
            (Comment.status == "approved", "published"),
            (Comment.status == "pending", "draft"),
            else_="archived",
        )
        updated = func.coalesce(Comment.updated_at, Comment.created_at)
        async for row in await session.stream_scalars(
            _within(select(Comment), request, states, updated, Comment.id)
        ):
            add(
                _item(
                    "comment",
                    row,
                    row.author,
                    status_of_comment(row),
                    comment_record(row),
                    row.updated_at or row.created_at,
                )
            )
    if "template" in kinds and (not request.statuses or "draft" in request.statuses):
        changed = (
            select(func.max(SettingsAudit.created_at))
            .where(SettingsAudit.resource == func.concat("page-template:", PageTemplate.id))
            .correlate(PageTemplate)
            .scalar_subquery()
        )
        updated = func.coalesce(changed, UNDATED)
        template_query = select(PageTemplate, updated).order_by(updated.desc(), PageTemplate.id)
        if request.date_from:
            template_query = template_query.where(updated >= request.date_from)
        if request.date_to:
            template_query = template_query.where(updated <= request.date_to)
        async for template, at in await session.stream(
            template_query.limit(MAX_ITEMS + 1).execution_options(yield_per=10)
        ):
            add(_item("template", template, template.title, "draft", template_record(template), at))
    if "site" in kinds and (not request.statuses or "published" in request.statuses):
        site = await session.get(SiteSettings, 1)
        at = (
            await session.scalar(
                select(func.max(SettingsAudit.created_at)).where(
                    SettingsAudit.resource == "public-settings"
                )
            )
            or UNDATED
        )
        if not (
            (request.date_from and at < request.date_from)
            or (request.date_to and at > request.date_to)
        ):
            stored = public_settings(site.data if site else {})
            add(
                MaintenanceBundleItem(
                    kind="site",
                    id=SITE_ID,
                    title=stored.site.name,
                    status="published",
                    updated_at=at,
                    value=stored.model_dump(
                        mode="json", include={"site", "theme", "theme_options"}
                    ),
                )
            )
    return items
