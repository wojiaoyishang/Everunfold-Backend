"""Title-based initial paths; once saved the path no longer tracks title changes."""

import re
import unicodedata
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.content.draft_models import ContentDraft
from app.modules.content.editor_schema import content_path
from app.modules.content.models import Content


def title_path(title: str, identity: UUID) -> str:
    slug = re.sub(
        r"[^\w-]+", "-", unicodedata.normalize("NFC", title).strip(), flags=re.UNICODE
    ).strip("-_")[:160]
    if not slug:
        slug = str(identity)
    return content_path(f"/{slug}.html")


async def initial_path(session: AsyncSession, title: str, identity: UUID) -> str:
    candidate = title_path(title, identity)
    root = candidate[:-5]
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
        if occupied is None and other_draft is None:
            return candidate
        counter += 1
        candidate = f"{root}-{counter}.html"
