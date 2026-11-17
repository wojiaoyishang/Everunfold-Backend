"""Private settings and moderation applied within the comment transaction."""

import re
import unicodedata
from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from pydantic import HttpUrl, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import User
from app.modules.auth.service import AuthFailure
from app.modules.comments.discussion_schema import (
    DISCUSSION_CONFIG_KEY,
    DiscussionSettings,
    normalized,
)
from app.modules.content.models import Comment
from app.modules.media.locking import lock_references
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService

SubmissionStatus = Literal["pending", "approved"]
WEBSITE_URL = TypeAdapter(HttpUrl)
LINK = re.compile(
    r"\b(?:https?://|mailto:|www\.)[^\s<>\])]+|\]\(\s*(?:/|\#)[^\s<>\])]+",
    re.IGNORECASE,
)


def settings_from_data(data: Mapping[str, object]) -> DiscussionSettings:
    value = data.get(DISCUSSION_CONFIG_KEY)
    if value is not None:
        return DiscussionSettings.model_validate(value)
    return DiscussionSettings(
        revision=1,
        allow_guest_comments=True,
        auto_approve_previously_approved=False,
        moderation_link_threshold=0,
        moderation_keywords=[],
        blocked_keywords=[],
    )


def comment_website(value: str | None) -> str | None:
    value = value.strip() if value else ""
    if not value:
        return None
    try:
        url = WEBSITE_URL.validate_python(value)
    except ValidationError:
        url = None
    if (
        url is None
        or not value.lower().startswith(("http://", "https://"))
        or "\\" in value
        or any(
            character.isspace() or unicodedata.category(character).startswith("C")
            for character in value
        )
        or url.username is not None
        or url.password is not None
        or len(str(url)) > 300
    ):
        raise AuthFailure(422, 42289, "网站地址必须是有效的 HTTP 或 HTTPS 地址")
    return str(url)


def require_comment_account(policy: DiscussionSettings, account: User | None) -> None:
    if policy.comments_disabled:
        raise AuthFailure(403, 40391, "全站评论已关闭")
    if account is None and not policy.allow_guest_comments:
        raise AuthFailure(403, 40389, "请登录后再发表评论")


def matches(fields: tuple[str, ...], terms: list[str]) -> bool:
    return any(normalized(term) in field for term in terms for field in fields)


async def classify_comment(
    session: AsyncSession,
    policy: DiscussionSettings,
    account: User | None,
    *,
    author: str,
    body: str,
    website: str | None,
) -> SubmissionStatus:
    fields = tuple(normalized(value) for value in (author, body, website or ""))
    if matches(fields, policy.blocked_keywords):
        raise AuthFailure(422, 42288, "这条评论不符合站点讨论规则")
    if (
        not policy.auto_approve_previously_approved
        or account is None
        or not account.email_verified_at
    ):
        return "pending"
    threshold = policy.moderation_link_threshold
    if matches(fields, policy.moderation_keywords) or (
        threshold and len(LINK.findall(fields[1])) + (website is not None) >= threshold
    ):
        return "pending"
    prior = await session.scalar(
        select(Comment.id)
        .where(Comment.author_user_id == account.id, Comment.status == "approved")
        .limit(1)
    )
    return "approved" if prior is not None else "pending"


async def save_discussion_settings(
    service: SettingsService, payload: DiscussionSettings, actor_id: UUID
) -> DiscussionSettings:
    await lock_references(service.session)
    site = await service.site(locked=True)
    current = settings_from_data(site.data)
    if current.revision != payload.revision:
        raise AuthFailure(409, 40920, "设置已被修改，请重新读取后合并你的更改")
    if "comments_disabled" not in payload.model_fields_set:
        payload = payload.model_copy(update={"comments_disabled": current.comments_disabled})
    previous, desired = current.model_dump(), payload.model_dump()
    changes = sorted(
        key for key in previous.keys() | desired.keys() if previous.get(key) != desired.get(key)
    )
    if not changes:
        return current
    saved = payload.model_copy(update={"revision": payload.revision + 1})
    site.data = {**site.data, DISCUSSION_CONFIG_KEY: saved.model_dump(mode="json")}
    if current.comments_disabled != saved.comments_disabled:
        await service.publish_site_projection(site)
    service.session.add(
        SettingsAudit(
            user_id=actor_id,
            resource="discussion-settings",
            revision=saved.revision,
            changes={"fields": changes},
        )
    )
    await service.session.commit()
    return saved
