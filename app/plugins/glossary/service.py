"""Glossary transactions, publication projection and collision fencing."""

import hashlib
import html
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import String, func, or_, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.content.editor_schema import EditorSettings
from app.modules.content.editor_service import editor_media
from app.modules.content.models import Content
from app.modules.content.schemas import MediaURL
from app.modules.pages.models import ManagedPage

from .matching import MatchRule, evaluate_rules
from .models import (
    GlossaryHook,
    GlossaryHookOperation,
    GlossaryOperation,
    GlossaryTerm,
)
from .schemas import (
    ContentGlossaryMatches,
    GlossaryBlock,
    GlossaryCreate,
    GlossaryDetail,
    GlossaryFields,
    GlossaryHookArchive,
    GlossaryHookCreate,
    GlossaryHookFields,
    GlossaryHookItem,
    GlossaryHookList,
    GlossaryHookMode,
    GlossaryHookQuery,
    GlossaryHookScope,
    GlossaryHookStatus,
    GlossaryHookTarget,
    GlossaryHookTargetKind,
    GlossaryHookUpdate,
    GlossaryList,
    GlossaryListItem,
    GlossaryMatchedText,
    GlossaryMatchPreviewInput,
    GlossaryMatchPreviewOutput,
    GlossaryMatchRule,
    GlossaryQuery,
    GlossaryStatus,
    GlossaryUpdate,
    PublicGlossaryDetail,
    PublicGlossaryHook,
    PublicGlossaryHooks,
    PublicGlossaryIndex,
    PublicGlossaryMatch,
    PublicGlossaryTerm,
)


def alias_key(value: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()


def term_targets(fields: GlossaryFields) -> list[str]:
    values = [fields.title] if fields.auto_match_title else []
    values.extend(fields.aliases)
    if fields.match_rules is not None:
        values.extend(
            rule.pattern for rule in fields.match_rules if rule.enabled and rule.kind == "literal"
        )
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(alias_key(value), value)
    return list(unique.values())


def configured_rules(
    title: str,
    aliases: list[str],
    auto_match_title: bool,
    match_rules: list[GlossaryMatchRule] | None,
) -> list[MatchRule]:
    values = ([title] if auto_match_title and title else []) + aliases
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(alias_key(value), value)
    rules = [MatchRule(kind="literal", pattern=value) for value in unique.values()]
    if match_rules is not None:
        rules.extend(
            MatchRule(
                kind=entry.kind,
                pattern=entry.pattern,
                enabled=entry.enabled,
                case_sensitive=entry.case_sensitive,
                whole_word=entry.whole_word,
                priority=entry.priority,
            )
            for entry in match_rules
        )
    return rules


def preview_matches(payload: GlossaryMatchPreviewInput) -> GlossaryMatchPreviewOutput:
    rules = configured_rules(
        payload.title,
        payload.aliases,
        payload.auto_match_title,
        payload.match_rules,
    )
    hits = evaluate_rules(unicodedata.normalize("NFKC", payload.sample), rules)
    return GlossaryMatchPreviewOutput(
        hits=[
            GlossaryMatchedText(
                text=hit.text,
                case_sensitive=hit.case_sensitive,
                whole_word=hit.whole_word,
                priority=hit.priority,
            )
            for hit in hits
        ]
    )


def operation_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def resolved_media(
    session: AsyncSession,
    config: Settings | None,
    body: list[dict[str, object]],
    *,
    strict: bool,
    feature_media_id: UUID | None = None,
) -> dict[str, MediaURL]:
    return await editor_media(
        session,
        config,
        json.dumps(body, ensure_ascii=False),
        EditorSettings(cover_media_id=feature_media_id),
        strict=strict,
    )


def row_feature_media_id(row: GlossaryTerm) -> UUID | None:
    return UUID(row.feature_media_id) if row.feature_media_id else None


async def resolved_feature_images(
    session: AsyncSession,
    config: Settings | None,
    rows: list[GlossaryTerm],
) -> dict[str, MediaURL]:
    ids = [feature_id for row in rows if (feature_id := row_feature_media_id(row))]
    if not ids or config is None:
        return {}
    return await editor_media(
        session,
        config,
        "\n".join(f"site://media/{item}" for item in ids),
        EditorSettings(),
        strict=False,
    )


async def detail(
    session: AsyncSession,
    row: GlossaryTerm,
    config: Settings | None = None,
) -> GlossaryDetail:
    feature_media_id = row_feature_media_id(row)
    media_urls = await resolved_media(
        session,
        config,
        row.body,
        strict=False,
        feature_media_id=feature_media_id,
    )
    return GlossaryDetail(
        id=row.id,
        language=row.language,
        slug=row.slug,
        title=row.title,
        summary=row.summary,
        feature_media_id=feature_media_id,
        aliases=row.aliases,
        auto_match_title=row.auto_match_title,
        match_rules=[GlossaryMatchRule.model_validate(rule) for rule in row.match_rules]
        if row.match_rules is not None
        else None,
        status=cast(GlossaryStatus, row.status),
        revision=row.revision,
        updated_at=row.updated_at,
        published_at=row.published_at,
        body=[GlossaryBlock.model_validate(block) for block in row.body],
        feature_image=media_urls.get(str(feature_media_id)) if feature_media_id else None,
        media_urls=media_urls,
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def public_item(
    row: GlossaryTerm,
    feature_image: MediaURL | None = None,
) -> PublicGlossaryTerm:
    return PublicGlossaryTerm(
        id=row.id,
        language=row.language,
        slug=row.slug,
        title=row.title,
        summary=row.summary,
        feature_image=feature_image,
        aliases=row.aliases,
        path=f"/glossary/{row.slug}",
    )


async def lock_glossary(session: AsyncSession) -> None:
    """Serialize glossary writes so wildcard and language-specific targets cannot race."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": "everunfold:glossary"},
    )


async def validate_targets(
    session: AsyncSession,
    fields: GlossaryFields,
    *,
    exclude_id: UUID | None = None,
) -> None:
    claimed = {alias_key(value): value for value in term_targets(fields)}
    statement = select(GlossaryTerm).where(GlossaryTerm.status != "archived")
    if exclude_id is not None:
        statement = statement.where(GlossaryTerm.id != exclude_id)
    for row in (await session.scalars(statement)).all():
        overlaps = fields.language == "*" or row.language == "*" or row.language == fields.language
        if not overlaps:
            continue
        if alias_key(row.slug) == alias_key(fields.slug):
            raise AuthFailure(409, 40936, f"公开路径“{fields.slug}”已被其他词条使用")
        row_targets = {
            alias_key(value)
            for value in term_targets(
                GlossaryFields.model_validate(
                    {
                        "language": row.language,
                        "slug": row.slug,
                        "title": row.title,
                        "summary": row.summary,
                        "body": row.body,
                        "aliases": row.aliases,
                        "auto_match_title": row.auto_match_title,
                        "match_rules": row.match_rules,
                        "status": row.status,
                    }
                )
            )
        }
        conflict = next((value for key, value in claimed.items() if key in row_targets), None)
        if conflict is not None:
            raise AuthFailure(409, 40936, f"术语或别名“{conflict}”已被其他词条使用")


async def replay_operation(
    session: AsyncSession,
    operation_id: UUID,
    fingerprint: str,
    config: Settings | None = None,
) -> GlossaryDetail | None:
    operation = await session.get(GlossaryOperation, operation_id)
    if operation is None:
        return None
    if operation.fingerprint != fingerprint:
        raise AuthFailure(409, 40937, "同一操作编号不能用于不同请求")
    row = await session.get(GlossaryTerm, operation.term_id)
    if row is None:
        raise AuthFailure(409, 40937, "原操作对应的词条已不存在")
    return await detail(session, row, config)


async def create_term(
    session: AsyncSession,
    actor: Actor,
    payload: GlossaryCreate,
    config: Settings | None = None,
) -> GlossaryDetail:
    fingerprint = operation_fingerprint(payload.model_dump(mode="json"))
    replayed = await replay_operation(session, payload.operation_id, fingerprint, config)
    if replayed is not None:
        return replayed
    await lock_glossary(session)
    await validate_targets(session, payload.fields)
    body = [block.model_dump(mode="json") for block in payload.fields.body]
    await resolved_media(
        session,
        config,
        body,
        strict=True,
        feature_media_id=payload.fields.feature_media_id,
    )
    now = datetime.now(UTC)
    row = GlossaryTerm(
        id=payload.term_id,
        language=payload.fields.language,
        slug=payload.fields.slug,
        title=payload.fields.title,
        summary=payload.fields.summary,
        feature_media_id=str(payload.fields.feature_media_id or ""),
        body_markdown="",
        body=body,
        aliases=payload.fields.aliases,
        auto_match_title=payload.fields.auto_match_title,
        match_rules=[rule.model_dump(mode="json") for rule in payload.fields.match_rules]
        if payload.fields.match_rules is not None
        else None,
        status=payload.fields.status,
        revision=1,
        created_by=actor.id,
        updated_by=actor.id,
        created_at=now,
        updated_at=now,
        published_at=now if payload.fields.status == "published" else None,
    )
    session.add(row)
    await session.flush()
    session.add(
        GlossaryOperation(
            operation_id=payload.operation_id,
            term_id=row.id,
            actor_id=actor.id,
            fingerprint=fingerprint,
            revision=1,
            created_at=now,
        )
    )
    await session.flush()
    return await detail(session, row, config)


async def update_term(
    session: AsyncSession,
    actor: Actor,
    term_id: UUID,
    payload: GlossaryUpdate,
    config: Settings | None = None,
) -> GlossaryDetail:
    fingerprint = operation_fingerprint(
        {
            "term_id": str(term_id),
            "expected_revision": payload.expected_revision,
            "fields": payload.fields.model_dump(mode="json"),
        }
    )
    replayed = await replay_operation(session, payload.operation_id, fingerprint, config)
    if replayed is not None:
        return replayed
    await lock_glossary(session)
    row = await session.scalar(
        select(GlossaryTerm).where(GlossaryTerm.id == term_id).with_for_update()
    )
    if row is None:
        raise AuthFailure(404, 40436, "术语不存在")
    if row.revision != payload.expected_revision:
        raise AuthFailure(409, 40938, "术语已在其他窗口更新，请载入最新版本")
    await validate_targets(session, payload.fields, exclude_id=term_id)
    body = [block.model_dump(mode="json") for block in payload.fields.body]
    await resolved_media(
        session,
        config,
        body,
        strict=True,
        feature_media_id=payload.fields.feature_media_id,
    )
    now = datetime.now(UTC)
    previous_status = row.status
    row.language = payload.fields.language
    row.slug = payload.fields.slug
    row.title = payload.fields.title
    row.summary = payload.fields.summary
    row.feature_media_id = str(payload.fields.feature_media_id or "")
    row.body_markdown = ""
    row.body = body
    row.aliases = payload.fields.aliases
    row.auto_match_title = payload.fields.auto_match_title
    row.match_rules = (
        [rule.model_dump(mode="json") for rule in payload.fields.match_rules]
        if payload.fields.match_rules is not None
        else None
    )
    row.status = payload.fields.status
    row.revision += 1
    row.updated_by = actor.id
    row.updated_at = now
    if row.status == "published" and (previous_status != "published" or row.published_at is None):
        row.published_at = now
    session.add(
        GlossaryOperation(
            operation_id=payload.operation_id,
            term_id=row.id,
            actor_id=actor.id,
            fingerprint=fingerprint,
            revision=row.revision,
            created_at=now,
        )
    )
    await session.flush()
    return await detail(session, row, config)


async def list_terms(
    session: AsyncSession,
    query: GlossaryQuery,
    config: Settings | None = None,
) -> GlossaryList:
    conditions = []
    if query.status != "all":
        conditions.append(GlossaryTerm.status == query.status)
    if query.language:
        conditions.append(GlossaryTerm.language == query.language)
    if query.search.strip():
        needle = f"%{query.search.strip()}%"
        conditions.append(
            or_(
                GlossaryTerm.title.ilike(needle),
                GlossaryTerm.slug.ilike(needle),
                GlossaryTerm.summary.ilike(needle),
                sql_cast(GlossaryTerm.aliases, String).ilike(needle),
            )
        )
    where = conditions or [GlossaryTerm.id.is_not(None)]
    total = int(
        await session.scalar(select(func.count()).select_from(GlossaryTerm).where(*where)) or 0
    )
    rows = (
        await session.scalars(
            select(GlossaryTerm)
            .where(*where)
            .order_by(GlossaryTerm.updated_at.desc(), GlossaryTerm.id)
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    features = await resolved_feature_images(session, config, list(rows))
    return GlossaryList(
        items=[
            GlossaryListItem(
                id=row.id,
                language=row.language,
                slug=row.slug,
                title=row.title,
                summary=row.summary,
                feature_media_id=row_feature_media_id(row),
                feature_image=features.get(row.feature_media_id) if row.feature_media_id else None,
                aliases=row.aliases,
                auto_match_title=row.auto_match_title,
                match_rules=[GlossaryMatchRule.model_validate(rule) for rule in row.match_rules]
                if row.match_rules is not None
                else None,
                status=cast(GlossaryStatus, row.status),
                revision=row.revision,
                updated_at=row.updated_at,
                published_at=row.published_at,
            )
            for row in rows
        ],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def read_term(
    session: AsyncSession,
    term_id: UUID,
    config: Settings | None = None,
) -> GlossaryDetail:
    row = await session.get(GlossaryTerm, term_id)
    if row is None:
        raise AuthFailure(404, 40436, "术语不存在")
    return await detail(session, row, config)


async def public_index(
    session: AsyncSession,
    language: str | None,
    config: Settings | None = None,
) -> PublicGlossaryIndex:
    statement = select(GlossaryTerm).where(GlossaryTerm.status == "published")
    if language:
        statement = statement.where(
            or_(GlossaryTerm.language == language, GlossaryTerm.language == "*")
        )
    rows = (await session.scalars(statement.order_by(GlossaryTerm.title, GlossaryTerm.id))).all()
    features = await resolved_feature_images(session, config, list(rows))
    revision = max((row.updated_at for row in rows), default=datetime(1970, 1, 1, tzinfo=UTC))
    return PublicGlossaryIndex(
        revision=revision.isoformat(),
        terms=[
            public_item(row, features.get(row.feature_media_id) if row.feature_media_id else None)
            for row in rows
        ],
    )


async def public_term(
    session: AsyncSession,
    slug: str,
    language: str | None,
    config: Settings | None = None,
) -> PublicGlossaryDetail:
    statement = select(GlossaryTerm).where(
        GlossaryTerm.status == "published",
        GlossaryTerm.slug == slug,
    )
    if language:
        statement = statement.where(
            or_(GlossaryTerm.language == language, GlossaryTerm.language == "*")
        )
    row = await session.scalar(statement.order_by(GlossaryTerm.language.desc()))
    if row is None:
        raise AuthFailure(404, 40436, "术语不存在")
    feature_media_id = row_feature_media_id(row)
    media_urls = await resolved_media(
        session,
        config,
        row.body,
        strict=False,
        feature_media_id=feature_media_id,
    )
    item = public_item(
        row,
        media_urls.get(str(feature_media_id)) if feature_media_id else None,
    )
    return PublicGlossaryDetail(
        **item.model_dump(),
        body=[GlossaryBlock.model_validate(block) for block in row.body],
        media_urls=media_urls,
    )


CODE_FENCE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
INLINE_CODE = re.compile(r"`[^`\n]+`")
HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")
HTML_LINK = re.compile(r"<a\b[^>]*>[\s\S]*?</a>", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
HTML_TAG = re.compile(r"<[^>]+>")
ASCII_WORD = re.compile(r"[A-Za-z0-9]")


def searchable_source(blocks: list[dict[str, object]]) -> str:
    """Only visible prose participates; code, markup and authored links are excluded."""
    pieces: list[str] = []
    for block in blocks:
        source = str(block.get("content", ""))
        source = CODE_FENCE.sub(" ", source)
        source = INLINE_CODE.sub(" ", source)
        source = HTML_COMMENT.sub(" ", source)
        source = HTML_LINK.sub(" ", source)
        source = MARKDOWN_LINK.sub(" ", source)
        source = HTML_TAG.sub(" ", source)
        pieces.append(html.unescape(source))
    return unicodedata.normalize("NFKC", "\n".join(pieces))


def target_in_source(source: str, value: str) -> bool:
    """Match prose without treating an ASCII term as part of a larger word."""
    source = source.casefold()
    needle = unicodedata.normalize("NFKC", value).casefold()
    offset = 0
    while offset < len(source):
        start = source.find(needle, offset)
        if start < 0:
            return False
        end = start + len(needle)
        left_safe = (
            not ASCII_WORD.match(needle[0]) or start == 0 or not ASCII_WORD.match(source[start - 1])
        )
        right_safe = (
            not ASCII_WORD.match(needle[-1])
            or end == len(source)
            or not ASCII_WORD.match(source[end])
        )
        if left_safe and right_safe:
            return True
        offset = start + 1
    return False


async def content_matches(
    session: AsyncSession,
    content_id: UUID,
    config: Settings | None = None,
) -> ContentGlossaryMatches:
    content = await session.get(Content, content_id)
    if (
        content is None
        or content.kind not in {"article", "shuoshuo"}
        or content.publication_status != "published"
        or content.visibility != "public"
    ):
        return ContentGlossaryMatches(content_id=content_id, revision="empty", terms=[])
    source = searchable_source(content.content)
    rows = (
        await session.scalars(
            select(GlossaryTerm).where(
                GlossaryTerm.status == "published",
                or_(
                    GlossaryTerm.language == content.language,
                    GlossaryTerm.language == "*",
                ),
            )
        )
    ).all()
    matches: list[PublicGlossaryMatch] = []
    latest = datetime(1970, 1, 1, tzinfo=UTC)
    for row in rows:
        rules = configured_rules(
            row.title,
            row.aliases,
            row.auto_match_title,
            [GlossaryMatchRule.model_validate(raw) for raw in row.match_rules]
            if row.match_rules is not None
            else None,
        )
        visible = evaluate_rules(source, rules)
        if not visible:
            continue
        feature_media_id = row_feature_media_id(row)
        media = await resolved_media(
            session,
            config,
            row.body,
            strict=False,
            feature_media_id=feature_media_id,
        )
        item = public_item(
            row,
            media.get(str(feature_media_id)) if feature_media_id else None,
        )
        matches.append(
            PublicGlossaryMatch(
                **item.model_dump(),
                body=[GlossaryBlock.model_validate(block) for block in row.body],
                media_urls=media,
                matched_texts=[hit.text for hit in visible],
                matched_patterns=[
                    GlossaryMatchedText(
                        text=hit.text,
                        case_sensitive=hit.case_sensitive,
                        whole_word=hit.whole_word,
                        priority=hit.priority,
                    )
                    for hit in visible
                ],
            )
        )
        latest = max(latest, row.updated_at)
    return ContentGlossaryMatches(
        content_id=content_id,
        revision=latest.isoformat(),
        terms=matches,
    )


def hook_fields_dump(fields: GlossaryHookFields) -> dict[str, object]:
    return fields.model_dump(mode="json")


async def validate_hook_targets(
    session: AsyncSession,
    fields: GlossaryHookFields,
) -> None:
    for target in fields.targets:
        if target.kind == "page":
            exists = await session.scalar(
                select(ManagedPage.id).where(
                    ManagedPage.id == target.id,
                    ManagedPage.deleted_at.is_(None),
                    ManagedPage.published.is_not(None),
                )
            )
        else:
            exists = await session.scalar(
                select(Content.id).where(
                    Content.id == target.id,
                    Content.kind == target.kind,
                    Content.publication_status == "published",
                    Content.visibility == "public",
                )
            )
        if exists is None:
            raise AuthFailure(422, 42239, "触发目标不存在或尚未公开")


async def admin_hook_item(
    session: AsyncSession,
    hook: GlossaryHook,
    term: GlossaryTerm,
) -> GlossaryHookItem:
    return GlossaryHookItem(
        id=hook.id,
        term=public_item(term),
        mode=cast(GlossaryHookMode, hook.mode),
        selector=hook.selector,
        script=hook.script,
        scope=cast(GlossaryHookScope, hook.scope),
        targets=[GlossaryHookTarget.model_validate(value) for value in hook.targets],
        enabled=hook.enabled,
        priority=hook.priority,
        status=cast(GlossaryHookStatus, hook.status),
        revision=hook.revision,
        updated_at=hook.updated_at,
    )


async def hook_detail(
    session: AsyncSession,
    hook_id: UUID,
) -> GlossaryHookItem:
    row = (
        await session.execute(
            select(GlossaryHook, GlossaryTerm)
            .join(GlossaryTerm, GlossaryTerm.id == GlossaryHook.term_id)
            .where(GlossaryHook.id == hook_id)
        )
    ).one_or_none()
    if row is None:
        raise AuthFailure(404, 40439, "术语触发不存在")
    return await admin_hook_item(session, row.GlossaryHook, row.GlossaryTerm)


async def replay_hook_operation(
    session: AsyncSession,
    operation_id: UUID,
    fingerprint: str,
) -> GlossaryHookItem | None:
    operation = await session.get(GlossaryHookOperation, operation_id)
    if operation is None:
        return None
    if operation.fingerprint != fingerprint:
        raise AuthFailure(409, 40939, "同一操作编号不能用于不同触发请求")
    return await hook_detail(session, operation.hook_id)


async def create_hook(
    session: AsyncSession,
    actor: Actor,
    payload: GlossaryHookCreate,
) -> GlossaryHookItem:
    fingerprint = operation_fingerprint(payload.model_dump(mode="json"))
    replayed = await replay_hook_operation(session, payload.operation_id, fingerprint)
    if replayed is not None:
        return replayed
    await lock_glossary(session)
    if await session.get(GlossaryHook, payload.hook_id) is not None:
        raise AuthFailure(409, 40939, "术语触发标识已存在")
    term = await session.get(GlossaryTerm, payload.term_id)
    if term is None:
        raise AuthFailure(404, 40436, "术语不存在")
    if payload.fields.mode == "script" and actor.role != "admin":
        raise AuthFailure(403, 40339, "只有管理员可以创建 JavaScript 页面触发")
    await validate_hook_targets(session, payload.fields)
    now = datetime.now(UTC)
    hook = GlossaryHook(
        id=payload.hook_id,
        term_id=payload.term_id,
        mode=payload.fields.mode,
        selector=payload.fields.selector,
        script=payload.fields.script,
        scope=payload.fields.scope,
        targets=[target.model_dump(mode="json") for target in payload.fields.targets],
        enabled=payload.fields.enabled,
        priority=payload.fields.priority,
        status="active",
        revision=1,
        created_by=actor.id,
        updated_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(hook)
    await session.flush()
    session.add(
        GlossaryHookOperation(
            operation_id=payload.operation_id,
            hook_id=hook.id,
            actor_id=actor.id,
            fingerprint=fingerprint,
            revision=1,
            created_at=now,
        )
    )
    await session.flush()
    return await admin_hook_item(session, hook, term)


async def update_hook(
    session: AsyncSession,
    actor: Actor,
    hook_id: UUID,
    payload: GlossaryHookUpdate,
) -> GlossaryHookItem:
    fingerprint = operation_fingerprint(
        {
            "hook_id": str(hook_id),
            "expected_revision": payload.expected_revision,
            "term_id": str(payload.term_id),
            "fields": hook_fields_dump(payload.fields),
        }
    )
    replayed = await replay_hook_operation(session, payload.operation_id, fingerprint)
    if replayed is not None:
        return replayed
    await lock_glossary(session)
    hook = await session.scalar(
        select(GlossaryHook).where(GlossaryHook.id == hook_id).with_for_update()
    )
    if hook is None:
        raise AuthFailure(404, 40439, "术语触发不存在")
    if hook.revision != payload.expected_revision:
        raise AuthFailure(409, 40940, "术语触发已在其他窗口更新")
    term = await session.get(GlossaryTerm, payload.term_id)
    if term is None:
        raise AuthFailure(404, 40436, "术语不存在")
    if payload.fields.mode == "script" and actor.role != "admin":
        raise AuthFailure(403, 40339, "只有管理员可以修改 JavaScript 页面触发")
    await validate_hook_targets(session, payload.fields)
    now = datetime.now(UTC)
    hook.term_id = payload.term_id
    hook.mode = payload.fields.mode
    hook.selector = payload.fields.selector
    hook.script = payload.fields.script
    hook.scope = payload.fields.scope
    hook.targets = [target.model_dump(mode="json") for target in payload.fields.targets]
    hook.enabled = payload.fields.enabled
    hook.priority = payload.fields.priority
    hook.status = "active"
    hook.revision += 1
    hook.updated_by = actor.id
    hook.updated_at = now
    session.add(
        GlossaryHookOperation(
            operation_id=payload.operation_id,
            hook_id=hook.id,
            actor_id=actor.id,
            fingerprint=fingerprint,
            revision=hook.revision,
            created_at=now,
        )
    )
    await session.flush()
    return await admin_hook_item(session, hook, term)


async def archive_hook(
    session: AsyncSession,
    actor: Actor,
    hook_id: UUID,
    payload: GlossaryHookArchive,
) -> GlossaryHookItem:
    fingerprint = operation_fingerprint(
        {
            "hook_id": str(hook_id),
            "expected_revision": payload.expected_revision,
            "status": "archived",
        }
    )
    replayed = await replay_hook_operation(session, payload.operation_id, fingerprint)
    if replayed is not None:
        return replayed
    await lock_glossary(session)
    row = (
        await session.execute(
            select(GlossaryHook, GlossaryTerm)
            .join(GlossaryTerm, GlossaryTerm.id == GlossaryHook.term_id)
            .where(GlossaryHook.id == hook_id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise AuthFailure(404, 40439, "术语触发不存在")
    hook = row.GlossaryHook
    if hook.revision != payload.expected_revision:
        raise AuthFailure(409, 40940, "术语触发已在其他窗口更新")
    now = datetime.now(UTC)
    hook.status = "archived"
    hook.enabled = False
    hook.revision += 1
    hook.updated_by = actor.id
    hook.updated_at = now
    session.add(
        GlossaryHookOperation(
            operation_id=payload.operation_id,
            hook_id=hook.id,
            actor_id=actor.id,
            fingerprint=fingerprint,
            revision=hook.revision,
            created_at=now,
        )
    )
    await session.flush()
    return await admin_hook_item(session, hook, row.GlossaryTerm)


async def list_hooks(session: AsyncSession, query: GlossaryHookQuery) -> GlossaryHookList:
    conditions = []
    if query.status != "all":
        conditions.append(GlossaryHook.status == query.status)
    if query.term_id is not None:
        conditions.append(GlossaryHook.term_id == query.term_id)
    if query.search.strip():
        needle = f"%{query.search.strip()}%"
        conditions.append(
            or_(
                GlossaryTerm.title.ilike(needle),
                GlossaryHook.selector.ilike(needle),
            )
        )
    where = conditions or [GlossaryHook.id.is_not(None)]
    joined = (
        select(GlossaryHook, GlossaryTerm)
        .join(GlossaryTerm, GlossaryTerm.id == GlossaryHook.term_id)
        .where(*where)
    )
    total = int(
        await session.scalar(select(func.count()).select_from(joined.order_by(None).subquery()))
        or 0
    )
    rows = (
        await session.execute(
            joined.order_by(
                GlossaryHook.status,
                GlossaryHook.priority.desc(),
                GlossaryHook.updated_at.desc(),
                GlossaryHook.id,
            )
            .offset((query.page - 1) * query.page_size)
            .limit(query.page_size)
        )
    ).all()
    return GlossaryHookList(
        items=[await admin_hook_item(session, row.GlossaryHook, row.GlossaryTerm) for row in rows],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


def hook_targets_surface(
    hook: GlossaryHook,
    surface: str,
    content_id: UUID | None,
    page_id: UUID | None,
) -> bool:
    if hook.scope == "all":
        return True
    if hook.scope != "content":
        return False
    target_id = page_id if surface == "page" else content_id
    if target_id is None:
        return False
    kind: GlossaryHookTargetKind = cast(GlossaryHookTargetKind, surface)
    return any(
        value.get("kind") == kind and str(value.get("id")) == str(target_id)
        for value in hook.targets
    )


async def public_hooks(
    session: AsyncSession,
    surface: Literal["article", "shuoshuo", "page", "code"],
    content_id: UUID | None,
    page_id: UUID | None,
    language: str | None,
    config: Settings | None = None,
) -> PublicGlossaryHooks:
    statement = (
        select(GlossaryHook, GlossaryTerm)
        .join(GlossaryTerm, GlossaryTerm.id == GlossaryHook.term_id)
        .where(
            GlossaryHook.status == "active",
            GlossaryHook.enabled.is_(True),
            GlossaryTerm.status == "published",
        )
    )
    if language:
        statement = statement.where(
            or_(GlossaryTerm.language == language, GlossaryTerm.language == "*")
        )
    rows = (
        await session.execute(
            statement.order_by(
                GlossaryHook.priority.desc(),
                GlossaryHook.updated_at.desc(),
                GlossaryHook.id,
            ).limit(50)
        )
    ).all()
    hooks: list[PublicGlossaryHook] = []
    latest = datetime(1970, 1, 1, tzinfo=UTC)
    for row in rows:
        if not hook_targets_surface(row.GlossaryHook, surface, content_id, page_id):
            continue
        feature_media_id = row_feature_media_id(row.GlossaryTerm)
        media_urls = await resolved_media(
            session,
            config,
            row.GlossaryTerm.body,
            strict=False,
            feature_media_id=feature_media_id,
        )
        item = public_item(
            row.GlossaryTerm,
            media_urls.get(str(feature_media_id)) if feature_media_id else None,
        )
        term = PublicGlossaryDetail(
            **item.model_dump(),
            body=[GlossaryBlock.model_validate(block) for block in row.GlossaryTerm.body],
            media_urls=media_urls,
        )
        hooks.append(
            PublicGlossaryHook(
                id=row.GlossaryHook.id,
                mode=cast(GlossaryHookMode, row.GlossaryHook.mode),
                selector=row.GlossaryHook.selector,
                script=row.GlossaryHook.script,
                scope=cast(GlossaryHookScope, row.GlossaryHook.scope),
                priority=row.GlossaryHook.priority,
                term=term,
            )
        )
        latest = max(latest, row.GlossaryHook.updated_at, row.GlossaryTerm.updated_at)
    return PublicGlossaryHooks(revision=latest.isoformat(), hooks=hooks)
