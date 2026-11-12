"""Title-based initial paths; once saved the path no longer tracks title changes."""

import re
import unicodedata
from datetime import UTC, datetime
from posixpath import splitext
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.draft_models import ContentDraft
from app.modules.content.editor_schema import content_path
from app.modules.content.models import Content, SiteSettings
from app.modules.pages.paths import page_path_taken
from app.modules.settings.defaults import public_settings
from app.modules.settings.languages import language_catalog, reserved_language_path
from app.modules.settings.publishing_preferences import DEFAULT_PERMALINK


def title_path(
    title: str,
    identity: UUID,
    *,
    language: str = "zh-CN",
    prefix: str | None = None,
    template: str = DEFAULT_PERMALINK,
    date: datetime | None = None,
) -> str:
    slug = re.sub(
        r"[^\w-]+", "-", unicodedata.normalize("NFC", title).strip(), flags=re.UNICODE
    ).strip("-_")[:160]
    if not slug:
        slug = str(identity)
    prefix = prefix if prefix is not None else "" if language == "zh-CN" else "/" + language.lower()
    date = date or datetime.now(UTC)
    values = {
        "%postname%": slug,
        "%post_id%": str(identity),
        "%year%": f"{date.year:04}",
        "%monthnum%": f"{date.month:02}",
        "%day%": f"{date.day:02}",
    }
    path = re.sub(r"%[a-z_]+%", lambda match: values[match[0]], template)
    try:
        return content_path(prefix + path)
    except ValueError:
        # A title can equal a reserved route with an extensionless template.
        return content_path(f"{prefix}/{slug}-{identity.hex[:8]}.html")


async def initial_path(
    session: AsyncSession,
    title: str,
    identity: UUID,
    *,
    language: str = "zh-CN",
    type_id: str = "article",
    date: datetime | None = None,
) -> str:
    catalog = await language_catalog(session)
    entry = next((item for item in catalog.languages if item.code == language), None)
    row = await session.get(SiteSettings, 1) if type_id == "article" else None
    site = public_settings(row.data).site if row else None
    zone = ZoneInfo(site.reading.time_zone) if site else UTC
    candidate = title_path(
        title,
        identity,
        language=language,
        prefix=entry.prefix if entry else None,
        template=site.writing.permalink if site else DEFAULT_PERMALINK,
        date=(date or datetime.now(UTC)).astimezone(zone),
    )
    if reserved_language_path(candidate, catalog):
        candidate = title_path(
            title, identity, language=language, prefix=entry.prefix if entry else None
        )
    root, extension = splitext(candidate)
    counter = 1
    while True:
        occupied = await session.scalar(
            select(Content.id).where(Content.path == candidate, Content.id != identity).limit(1)
        )
        other_draft = await session.scalar(
            select(ContentDraft.id)
            .where(
                ContentDraft.id != identity,
                or_(
                    ContentDraft.public_path == candidate,
                    ContentDraft.editor_settings["path"].astext == candidate,
                ),
            )
            .limit(1)
        )
        if (
            occupied is None
            and other_draft is None
            and not await page_path_taken(session, candidate)
        ):
            return candidate
        counter += 1
        candidate = f"{root}-{counter}{extension}"
