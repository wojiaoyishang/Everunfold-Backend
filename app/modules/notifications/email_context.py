"""Resolve site branding and safe email URLs from stored settings and trusted configuration."""

import re
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.auth.service import AuthFailure
from app.modules.media.service import MediaService
from app.modules.notifications.schemas import MailBrand
from app.modules.settings.defaults import public_settings

BRAND_KEY = "_notification_mail_brand"
EMAIL_VARIABLES = (
    "site.name",
    "site.description",
    "site.author",
    "site.icon_url",
    "site.url",
    "site.support_url",
    "site.accent_color",
    "site.accent_text_color",
    "recipient.display_name",
    "mail.reason",
)
NOTIFICATION_EVENT_VARIABLES = ("event.id", "event.occurred_at")
URL_FIELDS = {
    "admin": ("url",),
    "comment": ("review_url", "approve_url", "reject_url"),
    "content": ("url",),
    "resource": ("url", "review_url"),
}


def stored_brand(data: dict[str, Any]) -> MailBrand:
    return MailBrand.model_validate(data.get(BRAND_KEY, {}))


def site_origin(config: Settings) -> str:
    for node in config.frontends.values():
        if not node.enabled:
            continue
        host = (urlsplit(node.base_url).hostname or "").rstrip(".")
        try:
            if ip_address(host).is_global:
                return node.base_url
        except ValueError:
            if "." in host and not host.endswith((".local", ".localhost", ".internal")):
                return node.base_url
    return ""


def absolute_url(value: str, origin: str) -> str:
    if re.search(r"[\x00-\x20\x7f\\]", value):
        return ""
    if value.startswith("/") and not value.startswith("//"):
        return origin + value if origin else ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and "@" not in parsed.netloc:
            _ = parsed.port
            return value
    except ValueError:
        pass
    return ""


def accent_text(color: str) -> str:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    red, green, blue = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "#ffffff" if luminance <= 0.179 else "#000000"


async def site_variables(
    session: AsyncSession, config: Settings, data: dict[str, Any], brand: MailBrand
) -> dict[str, str]:
    public = public_settings(data)
    site = public.site
    origin = brand.site_url or site_origin(config)
    icon = brand.icon_url
    if not icon and site.avatar_media_id:
        try:
            icons = await MediaService(session, config).resolve_resources(
                {site.avatar_media_id: "image"}
            )
            icon = icons[site.avatar_media_id]
        except AuthFailure as exc:
            if exc.status != 422:
                raise
    accent = brand.accent_color or public.theme.accent
    return {
        "name": site.name,
        "description": site.description,
        "author": site.author,
        "icon_url": absolute_url(icon or site.avatar_url, origin),
        "url": origin,
        "support_url": brand.support_url,
        "accent_color": accent,
        "accent_text_color": accent_text(accent),
    }


def comment_data(data: dict[str, Any], resource_id: str) -> dict[str, Any]:
    resource = data.get("resource", {})
    try:
        identity = UUID(resource_id)
    except ValueError:
        return data
    review_url = f"/admin/comments?comment={identity}"
    return {
        **data,
        "resource": {**resource, "review_url": review_url},
        "comment": {
            "author_name": data.get("actor", {}).get("name", ""),
            "excerpt": data.get("content", {}).get("excerpt", ""),
            "review_url": review_url,
            "approve_url": f"{review_url}&action=approve",
            "reject_url": f"{review_url}&action=reject",
        },
    }


def email_urls(data: dict[str, Any], origin: str) -> dict[str, Any]:
    return {
        **data,
        **{
            group: {
                **data[group],
                **{key: absolute_url(data[group].get(key, ""), origin) for key in keys},
            }
            for group, keys in URL_FIELDS.items()
            if isinstance(data.get(group), dict)
        },
    }


MAIL_REASONS = {
    "security.email_verification": (
        "你收到这封邮件是因为有人请求验证此邮箱。",
        "You received this email because verification was requested for this address.",
    ),
    "comment.pending_review": (
        "你收到这封邮件是因为站点为你配置了评论审核通知。",
        "You received this email because the site sends you comment moderation notifications.",
    ),
    "content.scheduled_published": (
        "你收到这封邮件是因为站点为你配置了定时发布通知。",
        "You received this email because the site sends you scheduled publication notifications.",
    ),
    "content.schedule_failed": (
        "你收到这封邮件是因为站点为你配置了定时发布失败通知。",
        "You received this email because the site sends you alerts about failed scheduled posts.",
    ),
    "settings.delivery_failed": (
        "你收到这封邮件是因为站点为你配置了站点同步失败通知。",
        "You received this email because the site sends you site sync failure notifications.",
    ),
}


def english(locale: str) -> bool:
    return locale.split("-", 1)[0].lower() == "en"


def mail_reason(event: str, locale: str) -> str:
    return MAIL_REASONS[event][english(locale)]
