"""Validate portable records and produce a read-only conflict and dependency plan."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.schemas import PublicModel
from app.modules.auth.service import AuthFailure
from app.modules.comments.discussion import comment_website
from app.modules.content.draft_models import ContentDraft, TranslationGroup
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.models import Comment, Content, Taxonomy, TaxonomyName
from app.modules.media.models import MediaAsset
from app.modules.pages.component_values import SOURCE_FIELDS, page_document, public_page_value
from app.modules.pages.models import ManagedPage, PageTemplate, PageTranslationGroup
from app.modules.pages.options import bounded_options
from app.modules.pages.paths import content_path_taken, page_path_taken
from app.modules.pages.schemas import CUSTOM_PAGE_TYPE, PageCode, TitledPageCode
from app.modules.pages.templates import effective_value, require_page_size
from app.modules.pages.types import RegisteredPageType, registered_types
from app.modules.settings.languages import LanguageCatalog, language_catalog, reserved_language_path
from app.modules.shortcodes.regions import PASSWORD_ARGUMENT, REGION_TOKEN, RegionPassword
from app.plugins.glossary.models import GlossaryTerm
from app.plugins.glossary.schemas import GlossaryFields
from app.plugins.glossary.service import alias_key, term_targets, validate_targets

from .records import (
    CommentRecord,
    DraftRecord,
    PageRecord,
    TaxonomyRecord,
    comment_record,
    draft_record,
    glossary_record,
    page_record,
    taxonomy_record,
    template_record,
)
from .schemas import (
    MaintenanceBundleItem,
    MaintenanceImportItem,
    MaintenanceKind,
    MaintenanceState,
    MaintenanceStatus,
)

Record = DraftRecord | PageRecord | GlossaryFields | TaxonomyRecord | CommentRecord | TitledPageCode
Key = tuple[MaintenanceKind, UUID]
UUID_PATTERN = r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
EXPLICIT_MEDIA = re.compile(rf"(?:media:|ef-font-|/media/\d{{4}}/\d{{2}}/)({UUID_PATTERN})")

RECORD_TYPES: dict[str, type[PublicModel]] = {
    "article": DraftRecord,
    "shuoshuo": DraftRecord,
    "page": PageRecord,
    "glossary": GlossaryFields,
    "category": TaxonomyRecord,
    "tag": TaxonomyRecord,
    "comment": CommentRecord,
    "template": TitledPageCode,
}


@dataclass(slots=True)
class Plan:
    item: MaintenanceImportItem
    source_status: MaintenanceStatus
    value: Record | None = None
    dependencies: set[Key] = field(default_factory=set)

    @property
    def key(self) -> Key:
        return self.item.kind, self.item.id

    def mark(self, state: MaintenanceState, reason: str) -> None:
        self.item.state = state
        self.item.reason = reason


def prepare(item: MaintenanceBundleItem) -> Record:
    model = RECORD_TYPES[item.kind]
    value = cast(Record, model.model_validate(item.value))
    if isinstance(value, PageRecord):
        clean = value.value.model_copy(update={"enabled": False})
        if clean.edit_mode == "components":
            for name in SOURCE_FIELDS:
                setattr(clean, name, getattr(PageCode(), name))
            clean.component_document = page_document(clean)
        value.value = clean
    if isinstance(value, TitledPageCode) and value.edit_mode == "components":
        for name in SOURCE_FIELDS:
            setattr(value, name, getattr(PageCode(), name))
        value.component_document = page_document(value)
    if isinstance(value, DraftRecord) and value.fields.settings is None:
        value.fields.settings = EditorSettings()
    if isinstance(value, CommentRecord):
        value.website = comment_website(value.website)
    if isinstance(value, GlossaryFields):
        value.status = "draft"
    return value


def same_input(current: PublicModel, incoming: PublicModel) -> bool:
    if isinstance(current, PageRecord):
        current.value.enabled = False
    return current.model_dump(mode="json") == incoming.model_dump(mode="json")


async def existing(
    session: AsyncSession, kind: MaintenanceKind, identity: UUID, value: Record
) -> tuple[bool, PublicModel | None]:
    if isinstance(value, DraftRecord):
        row = await session.get(ContentDraft, identity)
        if row is not None:
            return True, await draft_record(session, row) if row.type_id == kind else None
        return await session.get(Content, identity) is not None, None
    if isinstance(value, PageRecord):
        page = await session.get(ManagedPage, identity)
        return (True, await page_record(session, page)) if page else (False, None)
    if isinstance(value, GlossaryFields):
        term = await session.get(GlossaryTerm, identity)
        return (True, glossary_record(term)) if term else (False, None)
    if isinstance(value, TaxonomyRecord):
        taxon = await session.get(Taxonomy, identity)
        return (
            (True, taxonomy_record(taxon) if taxon.kind == kind else None)
            if taxon
            else (False, None)
        )
    if isinstance(value, CommentRecord):
        comment = await session.get(Comment, identity)
        return (True, comment_record(comment)) if comment else (False, None)
    template = await session.get(PageTemplate, identity)
    return (True, template_record(template)) if template else (False, None)


def require(allowed: bool, reason: str) -> None:
    if not allowed:
        raise AuthFailure(422, 42281, reason)


class Planner:
    def __init__(
        self,
        session: AsyncSession,
        config: Settings,
        languages: LanguageCatalog,
        page_types: dict[str, RegisteredPageType],
    ) -> None:
        self.session, self.config, self.languages, self.page_types = (
            session,
            config,
            languages,
            page_types,
        )
        self.plans: dict[Key, Plan] = {}
        self.paths: set[str] = set()
        self.names: set[tuple[MaintenanceKind, str, str]] = set()
        self.group_languages: set[tuple[str, UUID, str]] = set()
        self.group_sources: dict[tuple[str, UUID], UUID] = {}
        self.terms: list[GlossaryFields] = []

    async def analyze(self, items: list[MaintenanceBundleItem]) -> list[Plan]:
        counts = Counter((item.kind, item.id) for item in items)
        # Draft and publication records share one primary-key domain across the two kinds.
        drafts = Counter(item.id for item in items if item.kind in {"article", "shuoshuo"})
        result = []
        for item in items:
            plan = Plan(
                MaintenanceImportItem(
                    id=item.id, kind=item.kind, title=item.title, state="create", reason=None
                ),
                source_status=item.status,
            )
            result.append(plan)
            self.plans[plan.key] = plan
            if counts[plan.key] > 1 or (
                item.kind in {"article", "shuoshuo"} and drafts[item.id] > 1
            ):
                plan.mark("conflict", "数据包有重复记录标识。")
            elif item.kind == "site":
                plan.mark("blocked", "站点设置可供查阅。请在站点设置中手动应用所需配置。")
            else:
                try:
                    plan.value = prepare(item)
                    found, row = await existing(self.session, item.kind, item.id, plan.value)
                    if found:
                        if row is not None and same_input(row, plan.value):
                            plan.mark("skip", "该标识的记录已经存在。")
                        else:
                            plan.mark("conflict", "该标识已被不同的站内记录使用。")
                except AuthFailure as exc:
                    plan.mark("blocked", exc.message)
                except (ValueError, RecursionError):
                    plan.mark("blocked", "记录字段无效或超出大小限制。")
        for plan in result:
            if plan.item.state != "create":
                continue
            try:
                await self.validate(plan)
                if isinstance(plan.value, DraftRecord):
                    settings = plan.value.fields.settings
                    if settings and settings.visibility == "password":
                        plan.item.reason = "保留密码保护设置。发布前须重新设置文章密码。"
            except AuthFailure as exc:
                plan.mark("conflict" if exc.status == 409 else "blocked", exc.message)
            except (ValidationError, ValueError):
                plan.mark("blocked", "记录字段无效或超出大小限制。")
        self.resolve_dependencies(result)
        return result

    def resolve_dependencies(self, plans: list[Plan]) -> None:
        pending = {plan.key: plan for plan in plans if plan.item.state == "create"}
        while pending:
            ready = [key for key, plan in pending.items() if plan.dependencies.isdisjoint(pending)]
            if not ready:
                for plan in pending.values():
                    plan.mark("blocked", "依赖有循环或关联记录不可导入。")
                return
            for key in ready:
                plan = pending.pop(key)
                if any(
                    self.plans[dep].item.state not in {"create", "skip"}
                    for dep in plan.dependencies
                ):
                    plan.mark("blocked", "所需的关联记录不可导入。")

    async def validate(self, plan: Plan) -> None:
        value = plan.value
        assert value is not None
        if isinstance(value, DraftRecord):
            fields = value.fields
            require(plan.item.kind == "shuoshuo" or bool(fields.title.strip()), "文章需要标题。")
            require(plan.item.kind == "article" or not fields.excerpt, "说说不支持独立摘要。")
            settings = fields.settings or EditorSettings()
            body: object
            if fields.edit_mode == "components" and fields.component_document is not None:
                body = fields.component_document.model_dump(mode="json")
            else:
                body = [block.model_dump(mode="json") for block in fields.body]
            await self.check_media(body, settings)
            await self.check_media(fields.excerpt)
            require(
                not (settings.visibility == "public" and settings.opens_at),
                "公开文章不可设置定时开放。",
            )
            if settings.path:
                await self.path(settings.path)
            await self.group(plan, value.group_id, value.source_id, value.language)
            if settings.path:
                self.paths.add(settings.path)

        elif isinstance(value, PageRecord):
            if value.type_id == CUSTOM_PAGE_TYPE:
                group_id, source_id, language = value.group_id, value.source_id, value.language
                require(
                    group_id is not None and source_id is not None and language is not None,
                    "自定义页面缺少语言分组。",
                )
            else:
                group_id = source_id = language = None
                require(
                    value.language is None
                    and value.group_id is None
                    and value.source_id is None
                    and value.template_id is None,
                    "已注册页面的语言和代码由插件管理。",
                )
                definition = self.page_types.get(value.type_id)
                require(definition is not None, "此节点没有安装页面类型所需的插件。")
                assert definition is not None
                submitted = value.value
                submitted.options = bounded_options(
                    definition.definition.initial_options() | submitted.options
                )
                if value.type_id == "core.book" and submitted.options.get("category_id"):
                    await self.parent_category(plan, UUID(str(submitted.options["category_id"])))
                require(
                    submitted.edit_mode == "source"
                    and submitted.component_document is None
                    and not submitted.component_parameters
                    and all(
                        getattr(submitted, field) == getattr(PageCode(), field)
                        for field in SOURCE_FIELDS
                    ),
                    "已注册页面只支持页面配置。",
                )
                require(
                    definition.definition.seo_editable
                    or (not submitted.seo_overrides() and submitted.share_image_media_id is None),
                    "此页面类型不支持自定义搜索与分享设置。",
                )
            template = await self.page_template(plan, value.template_id)
            if value.template_id is not None:
                require(
                    not (
                        value.value.html
                        or value.value.css
                        or value.value.javascript
                        or value.value.javascript_enabled
                        or value.value.edit_mode != "source"
                        or value.value.component_document
                    ),
                    "使用模板的页面需要在模板中管理代码。",
                )
            page = effective_value(value.value.model_dump(mode="json"), template)
            require_page_size(page)
            public_page_value(page, linked=template is not None)
            await self.check_media(
                page.model_dump(mode="json"),
                EditorSettings(share_image_media_id=page.share_image_media_id),
            )
            await self.path(page.path)
            if group_id is not None and source_id is not None and language is not None:
                await self.group(plan, group_id, source_id, language)
            self.paths.add(page.path)

        elif isinstance(value, TaxonomyRecord):
            require(plan.item.kind == "category" or value.parent_id is None, "标签不能有父级。")
            if value.parent_id is not None:
                await self.parent_category(plan, value.parent_id)
            alias = await self.session.get(TaxonomyName, (plan.item.kind, value.name.casefold()))
            existing_taxon = await self.session.scalar(
                select(Taxonomy.id)
                .where(
                    Taxonomy.kind == plan.item.kind,
                    (Taxonomy.slug == value.slug)
                    | (func.lower(Taxonomy.name) == value.name.lower()),
                )
                .limit(1)
            )
            name = (plan.item.kind, "name", value.name.casefold())
            slug = (plan.item.kind, "slug", value.slug)
            if (
                alias is not None
                or existing_taxon is not None
                or name in self.names
                or slug in self.names
            ):
                raise AuthFailure(409, 40981, "同名或同地址的分类或标签已存在。")
            self.names.update((name, slug))

        elif isinstance(value, GlossaryFields):
            if value.language != "*":
                self.require_language(value.language)
            await validate_targets(self.session, value)
            if await self.session.scalar(
                select(GlossaryTerm.id).where(
                    GlossaryTerm.language == value.language,
                    GlossaryTerm.slug == value.slug,
                )
            ):
                raise AuthFailure(409, 40982, "该词条地址已在使用。")
            targets = {alias_key(target) for target in (*term_targets(value), value.slug)}
            for term in self.terms:
                if value.language not in {"*", term.language} and term.language != "*":
                    continue
                if targets & {alias_key(target) for target in (*term_targets(term), term.slug)}:
                    raise AuthFailure(409, 40982, "数据包中有同地址或同别名的词条。")
            await self.check_media(
                [block.model_dump(mode="json") for block in value.body],
                EditorSettings(
                    cover_media_id=value.feature_media_id,
                    share_image_media_id=value.share_image_media_id,
                ),
            )
            self.terms.append(value)

        elif isinstance(value, CommentRecord):
            await self.comment(plan, value)
            await self.check_media(value.body, editable_regions=False)

        elif isinstance(value, TitledPageCode):
            require_page_size(value)
            await self.check_media(value.model_dump(mode="json"))

    async def check_media(
        self, body: object, settings: EditorSettings | None = None, *, editable_regions: bool = True
    ) -> None:
        explicit: set[UUID] = set()

        # Work with the original text so shortcode attribute quotes are preserved.
        async def scan(value: object) -> None:
            if isinstance(value, dict):
                for field_value in value.values():
                    await scan(field_value)
            elif isinstance(value, list):
                for field_value in value:
                    await scan(field_value)
            elif isinstance(value, str):
                explicit.update(UUID(raw) for raw in EXPLICIT_MEDIA.findall(value))
                if not editable_regions:
                    return
                for match in REGION_TOKEN.finditer(value):
                    if not match[1] and match[2] == "password-area":
                        argument = PASSWORD_ARGUMENT.match(value, match.end())
                        credential = (
                            await self.session.get(RegionPassword, UUID(argument[1]))
                            if argument
                            else None
                        )
                        require(
                            credential is not None,
                            "密码保护区缺少本站凭据，请先在编辑器中重新配置。",
                        )

        await scan(body)
        if explicit:
            available = set(
                await self.session.scalars(
                    select(MediaAsset.id).where(
                        MediaAsset.id.in_(explicit),
                        MediaAsset.status == "ready",
                        MediaAsset.deleted_at.is_(None),
                        MediaAsset.removed_at.is_(None),
                        MediaAsset.purge_pending.is_(False),
                    )
                )
            )
            require(explicit == available, "部分媒体附件在本站不可用。")
        await editor_media(
            self.session,
            self.config,
            json.dumps(body, ensure_ascii=False),
            settings or EditorSettings(),
        )

    async def path(self, path: str) -> None:
        require(not reserved_language_path(path, self.languages), "地址与语言入口冲突。")
        if (
            path in self.paths
            or await page_path_taken(self.session, path)
            or await content_path_taken(self.session, path)
        ):
            raise AuthFailure(409, 40983, "该地址已被其他内容使用。")

    def require_language(self, language: str) -> None:
        require(
            any(item.code == language and item.enabled for item in self.languages.languages),
            "对应的内容语言尚未在站点启用。",
        )

    async def group(self, plan: Plan, group: UUID, source: UUID, language: str) -> None:
        self.require_language(language)
        page = plan.item.kind == "page"
        space = "page" if page else "content"
        group_key = (space, group)
        language_key = (*group_key, language)
        if (
            language_key in self.group_languages
            or self.group_sources.get(group_key, source) != source
        ):
            raise AuthFailure(409, 40984, "语言分组在数据包中有冲突。")
        persisted = (
            await self.session.get(PageTranslationGroup, group)
            if page
            else await self.session.get(TranslationGroup, group)
        )
        if persisted is not None:
            if (
                persisted.source_id != source
                or isinstance(persisted, TranslationGroup)
                and persisted.type_id != plan.item.kind
            ):
                raise AuthFailure(409, 40984, "语言分组标识已被其他内容占用。")
            table = ManagedPage if page else ContentDraft
            occupied = await self.session.scalar(
                select(table.id).where(
                    table.translation_group_id == group, table.language == language
                )
            )
            if occupied is not None:
                raise AuthFailure(409, 40984, "该语言分组已有这个语言版本。")
        elif source != plan.item.id:
            key = (plan.item.kind, source)
            packaged = self.plans.get(key)
            require(
                packaged is not None
                and isinstance(packaged.value, DraftRecord | PageRecord)
                and packaged.value.group_id == group
                and packaged.value.source_id == source,
                "缺少该语言分组的原文。",
            )
            plan.dependencies.add(key)
        self.group_sources[group_key] = source
        self.group_languages.add(language_key)

    async def page_template(self, plan: Plan, identity: UUID | None) -> PageTemplate | None:
        if identity is None:
            return None
        template = await self.session.get(PageTemplate, identity)
        key: Key = ("template", identity)
        packaged = self.plans.get(key)
        if packaged is not None:
            plan.dependencies.add(key)
            if template is None and isinstance(packaged.value, TitledPageCode):
                return PageTemplate(
                    id=identity,
                    title=packaged.value.title,
                    description=packaged.value.description,
                    revision=1,
                    code=packaged.value.model_dump(mode="json", exclude={"title", "description"}),
                )
        require(template is not None, "依赖的页面模板不存在。")
        return template

    async def parent_category(self, plan: Plan, identity: UUID) -> None:
        key: Key = ("category", identity)
        parent = await self.session.get(Taxonomy, identity)
        if parent is None and key in self.plans:
            require(self.plans[key].source_status != "archived", "父分类已回收。")
            plan.dependencies.add(key)
        else:
            require(
                parent is not None and parent.kind == "category" and not parent.retired,
                "缺少父分类。",
            )

    async def comment(self, plan: Plan, record: CommentRecord) -> None:
        if record.target_kind == "content":
            target = await self.session.get(Content, record.target_id)
            require(
                target is not None and target.publication_status == "published",
                "目标文章或说说未发布。可在发布后重新预检评论。",
            )
        else:
            key: Key = ("page", record.target_id)
            if key in self.plans:
                plan.dependencies.add(key)
            else:
                page = await self.session.get(ManagedPage, record.target_id)
                require(page is not None and page.deleted_at is None, "缺少评论所属页面。")
        if record.parent_id is None:
            return
        parent = await self.session.get(Comment, record.parent_id)
        key = ("comment", record.parent_id)
        if key in self.plans:
            plan.dependencies.add(key)
        packaged = self.plans.get(key)
        value = comment_record(parent) if parent else packaged.value if packaged else None
        require(
            isinstance(value, CommentRecord)
            and (value.target_kind, value.target_id) == (record.target_kind, record.target_id),
            "缺少同一内容下的父评论。",
        )


async def make_plan(
    session: AsyncSession, config: Settings, items: list[MaintenanceBundleItem]
) -> list[Plan]:
    page_types = (
        await registered_types(session, config)
        if any(item.kind == "page" for item in items)
        else {}
    )
    return await Planner(session, config, await language_catalog(session), page_types).analyze(
        items
    )
