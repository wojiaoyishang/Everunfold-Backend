"""Glossary transactions, publication projection and collision fencing."""

import hashlib
import html
import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import String, func, or_, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.permissions import Actor
from app.modules.auth.service import AuthFailure
from app.modules.content.models import Content

from .models import GlossaryOperation, GlossaryTerm
from .schemas import (
    ContentGlossaryMatches,
    GlossaryCreate,
    GlossaryDetail,
    GlossaryFields,
    GlossaryList,
    GlossaryListItem,
    GlossaryQuery,
    GlossaryStatus,
    GlossaryUpdate,
    PublicGlossaryDetail,
    PublicGlossaryIndex,
    PublicGlossaryMatch,
    PublicGlossaryTerm,
)


def alias_key(value: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()


def term_targets(fields: GlossaryFields) -> list[str]:
    values = [fields.title, *fields.aliases]
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(alias_key(value), value)
    return list(unique.values())


def operation_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def detail(row: GlossaryTerm) -> GlossaryDetail:
    return GlossaryDetail(
        id=row.id,
        language=row.language,
        slug=row.slug,
        title=row.title,
        summary=row.summary,
        aliases=row.aliases,
        status=cast(GlossaryStatus, row.status),
        revision=row.revision,
        updated_at=row.updated_at,
        published_at=row.published_at,
        body_markdown=row.body_markdown,
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def public_item(row: GlossaryTerm) -> PublicGlossaryTerm:
    return PublicGlossaryTerm(
        id=row.id,
        language=row.language,
        slug=row.slug,
        title=row.title,
        summary=row.summary,
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
                        "body_markdown": row.body_markdown,
                        "aliases": row.aliases,
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
) -> GlossaryDetail | None:
    operation = await session.get(GlossaryOperation, operation_id)
    if operation is None:
        return None
    if operation.fingerprint != fingerprint:
        raise AuthFailure(409, 40937, "同一操作编号不能用于不同请求")
    row = await session.get(GlossaryTerm, operation.term_id)
    if row is None:
        raise AuthFailure(409, 40937, "原操作对应的词条已不存在")
    return detail(row)


async def create_term(
    session: AsyncSession,
    actor: Actor,
    payload: GlossaryCreate,
) -> GlossaryDetail:
    fingerprint = operation_fingerprint(payload.model_dump(mode="json"))
    replayed = await replay_operation(session, payload.operation_id, fingerprint)
    if replayed is not None:
        return replayed
    await lock_glossary(session)
    await validate_targets(session, payload.fields)
    now = datetime.now(UTC)
    row = GlossaryTerm(
        id=payload.term_id,
        language=payload.fields.language,
        slug=payload.fields.slug,
        title=payload.fields.title,
        summary=payload.fields.summary,
        body_markdown=payload.fields.body_markdown,
        aliases=payload.fields.aliases,
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
    return detail(row)


async def update_term(
    session: AsyncSession,
    actor: Actor,
    term_id: UUID,
    payload: GlossaryUpdate,
) -> GlossaryDetail:
    fingerprint = operation_fingerprint(
        {
            "term_id": str(term_id),
            "expected_revision": payload.expected_revision,
            "fields": payload.fields.model_dump(mode="json"),
        }
    )
    replayed = await replay_operation(session, payload.operation_id, fingerprint)
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
    now = datetime.now(UTC)
    previous_status = row.status
    row.language = payload.fields.language
    row.slug = payload.fields.slug
    row.title = payload.fields.title
    row.summary = payload.fields.summary
    row.body_markdown = payload.fields.body_markdown
    row.aliases = payload.fields.aliases
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
    return detail(row)


async def list_terms(session: AsyncSession, query: GlossaryQuery) -> GlossaryList:
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
    return GlossaryList(
        items=[
            GlossaryListItem(
                id=row.id,
                language=row.language,
                slug=row.slug,
                title=row.title,
                summary=row.summary,
                aliases=row.aliases,
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


async def read_term(session: AsyncSession, term_id: UUID) -> GlossaryDetail:
    row = await session.get(GlossaryTerm, term_id)
    if row is None:
        raise AuthFailure(404, 40436, "术语不存在")
    return detail(row)


async def public_index(session: AsyncSession, language: str | None) -> PublicGlossaryIndex:
    statement = select(GlossaryTerm).where(GlossaryTerm.status == "published")
    if language:
        statement = statement.where(
            or_(GlossaryTerm.language == language, GlossaryTerm.language == "*")
        )
    rows = (await session.scalars(statement.order_by(GlossaryTerm.title, GlossaryTerm.id))).all()
    revision = max((row.updated_at for row in rows), default=datetime(1970, 1, 1, tzinfo=UTC))
    return PublicGlossaryIndex(
        revision=revision.isoformat(),
        terms=[public_item(row) for row in rows],
    )


async def public_term(
    session: AsyncSession, slug: str, language: str | None
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
    item = public_item(row)
    return PublicGlossaryDetail(**item.model_dump(), body_markdown=row.body_markdown)


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
    return unicodedata.normalize("NFKC", "\n".join(pieces)).casefold()


def target_in_source(source: str, value: str) -> bool:
    """Match prose without treating an ASCII term as part of a larger word."""
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


async def content_matches(session: AsyncSession, content_id: UUID) -> ContentGlossaryMatches:
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
        visible = {value for value in [row.title, *row.aliases] if target_in_source(source, value)}
        if not visible:
            continue
        item = public_item(row)
        matches.append(
            PublicGlossaryMatch(
                **item.model_dump(),
                body_markdown=row.body_markdown,
                matched_texts=sorted(visible, key=lambda value: (-len(value), value)),
            )
        )
        latest = max(latest, row.updated_at)
    return ContentGlossaryMatches(
        content_id=content_id,
        revision=latest.isoformat(),
        terms=matches,
    )
