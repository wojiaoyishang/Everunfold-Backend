"""Notification configuration, recipient routing, rendering and inbox."""

import re
from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.plugins.loader import plugin_catalog
from app.core.plugins.state import enabled_ids, installed_info
from app.modules.auth.models import User
from app.modules.auth.permissions import CAPABILITIES, user_permissions
from app.modules.auth.service import AuthFailure
from app.modules.content.models import SiteSettings
from app.modules.notifications.catalog import SECURITY_EVENT_IDS, default_template
from app.modules.notifications.email_context import (
    BRAND_KEY,
    EMAIL_VARIABLES,
    NOTIFICATION_EVENT_VARIABLES,
    comment_data,
    email_urls,
    mail_reason,
    site_variables,
    stored_brand,
)
from app.modules.notifications.events import event_registry, subject_address_current
from app.modules.notifications.models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationInbox,
    NotificationRevision,
    NotificationRoute,
    NotificationTemplate,
)
from app.modules.notifications.render import (
    TOKEN,
    header_value,
    render_html_template,
    render_template,
    sanitize_email_html,
)
from app.modules.notifications.routing import EMAIL, IN_APP, route_id
from app.modules.notifications.schemas import (
    MarkNotificationsRead,
    NotificationDeliveryView,
    NotificationEventView,
    NotificationInboxItem,
    NotificationPlatformView,
    NotificationRouteView,
    NotificationState,
    NotificationTemplateContent,
    NotificationTemplateView,
    SaveMailBrand,
    SaveNotificationRoutes,
    SaveNotificationTemplate,
)
from app.modules.notifications.schemas import (
    NotificationInbox as InboxState,
)
from app.modules.settings.languages import language_catalog
from app.modules.settings.mail_service import (
    default_mail_profile,
    mail_profile,
    mail_ready,
    stored_mail_profiles,
)
from app.modules.settings.models import SettingsAudit
from app.modules.settings.service import SettingsService


def mask_address(value: str) -> str:
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    return value[:2] + "***" if value else ""


class NotificationService:
    def __init__(self, session: AsyncSession, config: Settings) -> None:
        self.session = session
        self.config = config

    async def revision(self, *, locked: bool = False) -> NotificationRevision:
        statement = select(NotificationRevision).where(NotificationRevision.id == 1)
        row = await self.session.scalar(statement.with_for_update() if locked else statement)
        if row is None:
            row = NotificationRevision(id=1, revision=1)
            self.session.add(row)
            await self.session.flush()
        return row

    async def platforms(self) -> list[NotificationPlatformView]:
        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        email = default_mail_profile(data)
        _, profiles = stored_mail_profiles(data)
        installed = {item.id: item for item in installed_info(data)}
        enabled = enabled_ids(data)
        available: list[NotificationPlatformView] = [
            NotificationPlatformView(
                id=IN_APP,
                title="站内通知",
                description="后台通知中心，不依赖外部服务。",
                kind="direct",
                builtin=True,
                available=True,
                delivery_semantics="idempotent",
            ),
            NotificationPlatformView(
                id=EMAIL,
                title="邮件",
                description=(
                    f"使用 {len(profiles)} 套发信配置，默认配置负责未指定的提醒。"
                    if profiles
                    else "尚未添加邮件发信配置。"
                ),
                kind="direct",
                builtin=True,
                available=bool(email and email.enabled and mail_ready(email.value, email.password)),
                delivery_semantics="unknown-no-retry",
            ),
        ]
        for plugin in plugin_catalog().plugins:
            for platform in plugin.notifications:
                applied = installed.get(plugin.info.id)
                available.append(
                    NotificationPlatformView(
                        id=platform.id,
                        title=platform.title,
                        description=platform.description,
                        kind=platform.kind,
                        builtin=False,
                        available=bool(
                            plugin.info.id in enabled
                            and applied
                            and applied.build == plugin.info.build
                        ),
                        delivery_semantics=platform.delivery_semantics,
                    )
                )
        return available

    async def state(self) -> NotificationState:
        revision = await self.revision()
        site = await self.session.get(SiteSettings, 1)
        data = site.data if site else {}
        registry = event_registry(data)
        brand = stored_brand(data)
        locales = [
            item.code for item in (await language_catalog(self.session)).languages if item.enabled
        ]
        for fallback_locale in ("zh-CN", "en"):
            if fallback_locale not in locales:
                locales.append(fallback_locale)
        platforms = await self.platforms()
        custom = {
            (row.event_type, row.platform_id, row.locale): row
            for row in (await self.session.scalars(select(NotificationTemplate))).all()
        }
        templates: list[NotificationTemplateView] = []
        for entry in registry.values():
            event = entry.definition
            for platform in platforms:
                if not event.accepts_platform(platform.id):
                    continue
                for locale in locales:
                    row = custom.get((event.id, platform.id, locale))
                    fallback = default_template(
                        event.id, platform.id, locale, event_title=event.title
                    )
                    templates.append(
                        NotificationTemplateView(
                            event_type=event.id,
                            platform_id=platform.id,
                            locale=locale,
                            subject=row.subject if row and row.subject else fallback.subject,
                            text=row.text if row and row.text else fallback.text,
                            html=row.html if row else fallback.html,
                            html_enabled=(row.html_enabled if row else bool(fallback.html)),
                            href=fallback.href,
                            enabled=row.enabled if row else True,
                            custom=bool(row),
                            revision=row.revision if row else 0,
                            builtin=NotificationTemplateContent(
                                subject=fallback.subject,
                                text=fallback.text,
                                html=fallback.html,
                                html_enabled=bool(fallback.html),
                            )
                            if row and platform.id == EMAIL
                            else None,
                        )
                    )
        routes = [
            NotificationRouteView(
                id=row.id,
                event_type=row.event_type,
                recipient_type=cast(Any, row.recipient_type),
                recipient_value=row.recipient_value,
                platform_id=row.platform_id,
                mail_profile_id=(UUID(row.mail_profile_id) if row.mail_profile_id else None),
                template_key=row.template_key,
                enabled=(
                    row.enabled
                    or registry[row.event_type].definition.delivery_mode == "security_required"
                ),
            )
            for row in (
                await self.session.scalars(
                    select(NotificationRoute)
                    .where(NotificationRoute.event_type.in_(registry))
                    .order_by(
                        NotificationRoute.event_type,
                        NotificationRoute.platform_id,
                    )
                )
            ).all()
            if registry[row.event_type].definition.accepts_recipient(
                row.recipient_type, row.recipient_value
            )
            and registry[row.event_type].definition.accepts_platform(row.platform_id)
        ]
        configured = {row.event_type for row in routes}
        routes.extend(
            NotificationRouteView(
                id=route_id(event.id, "subject", "subject", EMAIL),
                event_type=event.id,
                recipient_type="subject",
                recipient_value="subject",
                platform_id=EMAIL,
                enabled=True,
            )
            for entry in registry.values()
            if (event := entry.definition).delivery_mode != "notification"
            and event.id not in configured
        )
        deliveries = (
            await self.session.execute(
                select(NotificationDelivery, User, NotificationEvent)
                .join(User, User.id == NotificationDelivery.user_id)
                .join(NotificationEvent, NotificationEvent.id == NotificationDelivery.event_id)
                .order_by(NotificationDelivery.created_at.desc())
                .limit(50)
            )
        ).all()
        return NotificationState(
            revision=revision.revision,
            events=[
                NotificationEventView(
                    id=entry.definition.id,
                    title=entry.definition.title,
                    description=entry.definition.description,
                    email_only=entry.definition.email_only,
                    requires_code=entry.definition.requires_code,
                    recipient_mode=entry.definition.recipient_mode,
                    delivery_mode=entry.definition.delivery_mode,
                    plugin_id=entry.plugin_id,
                    variables=[
                        *entry.definition.variables,
                        *(
                            NOTIFICATION_EVENT_VARIABLES
                            if entry.definition.delivery_mode == "notification"
                            else ()
                        ),
                    ],
                )
                for entry in registry.values()
            ],
            platforms=platforms,
            routes=routes,
            templates=templates,
            mail_brand=brand,
            site_variables=await site_variables(self.session, self.config, data, brand),
            email_variables=list(EMAIL_VARIABLES),
            deliveries=[
                NotificationDeliveryView(
                    id=delivery.id,
                    event_type=event.event_type,
                    user_id=user.id,
                    user_name=user.display_name,
                    platform_id=delivery.platform_id,
                    mail_profile_id=(
                        UUID(delivery.mail_profile_id) if delivery.mail_profile_id else None
                    ),
                    locale=delivery.locale,
                    status=cast(Any, delivery.status),
                    attempts=delivery.attempts,
                    error=delivery.error,
                    created_at=delivery.created_at,
                    finished_at=delivery.finished_at,
                )
                for delivery, user, event in deliveries
            ],
            locales=locales,
        )

    async def save_routes(
        self, payload: SaveNotificationRoutes, actor_id: UUID, *, manage_access: bool
    ) -> NotificationState:
        # Serialize with SMTP profile edits in the same lock order as save_brand.
        site = await self.session.get(SiteSettings, 1, populate_existing=True, with_for_update=True)
        revision = await self.revision(locked=True)
        if revision.revision != payload.expected_revision:
            raise AuthFailure(409, 40973, "通知策略已在其他窗口修改，请重新读取")
        platforms = {item.id for item in await self.platforms()}
        mail_data = site.data if site else {}
        registry = event_registry(mail_data)
        for item in payload.routes:
            entry = registry.get(item.event_type)
            if entry is None:
                raise AuthFailure(422, 42299, "未知通知事件")
            definition = entry.definition
            if not definition.accepts_recipient(item.recipient_type, item.recipient_value):
                raise AuthFailure(422, 42299, "此事件的接收对象固定，不能更改")
            if not definition.accepts_platform(item.platform_id):
                raise AuthFailure(422, 42299, "此通知事件仅支持邮件")
            if definition.delivery_mode == "security_required" and not item.enabled:
                raise AuthFailure(422, 42299, "必要的账号验证码不能停用")
            if item.platform_id not in platforms:
                raise AuthFailure(422, 42299, "通知平台不存在")
            if item.mail_profile_id is not None:
                if item.platform_id != EMAIL:
                    raise AuthFailure(422, 42299, "只有邮件平台可以选择发信配置")
                if mail_profile(mail_data, item.mail_profile_id) is None:
                    raise AuthFailure(422, 42299, "所选发信配置不存在")
            if item.recipient_type == "permission" and item.recipient_value not in CAPABILITIES:
                raise AuthFailure(422, 42299, "未知通知权限")
            if item.recipient_type == "role" and item.recipient_value not in {"admin", "user"}:
                raise AuthFailure(422, 42299, "未知通知角色")
            if item.recipient_type == "user":
                try:
                    user_id = UUID(item.recipient_value)
                except ValueError:
                    raise AuthFailure(422, 42299, "通知用户无效") from None
                if await self.session.get(User, user_id) is None:
                    raise AuthFailure(422, 42299, "通知用户不存在")
        routes = payload.routes
        writable = set(registry)
        if not manage_access:
            protected = (
                await self.session.scalars(
                    select(NotificationRoute).where(
                        NotificationRoute.event_type.in_(SECURITY_EVENT_IDS)
                    )
                )
            ).all()
            actual = {row.event_type: row for row in protected}
            for item in routes:
                if item.event_type not in SECURITY_EVENT_IDS:
                    continue
                row = actual.get(item.event_type)
                if row is None:
                    unchanged = (
                        registry[item.event_type].definition.delivery_mode != "notification"
                        and item.mail_profile_id is None
                        and item.enabled
                        and item.template_key == "default"
                    )
                else:
                    unchanged = (
                        (str(item.mail_profile_id) if item.mail_profile_id else None)
                        == row.mail_profile_id
                        and item.enabled == row.enabled
                        and item.template_key == row.template_key
                    )
                if not unchanged:
                    raise AuthFailure(403, 40373, "无权修改账号安全通知策略")
            # Older clients may omit newly added rules; never remove protected rules for them.
            routes = [item for item in routes if item.event_type not in SECURITY_EVENT_IDS]
            writable -= SECURITY_EVENT_IDS
        # Keep rules for disabled or temporarily absent plugins.
        await self.session.execute(
            delete(NotificationRoute).where(NotificationRoute.event_type.in_(writable))
        )
        now = datetime.now(UTC)
        self.session.add_all(
            [
                NotificationRoute(
                    id=route_id(
                        item.event_type, item.recipient_type, item.recipient_value, item.platform_id
                    ),
                    event_type=item.event_type,
                    recipient_type=item.recipient_type,
                    recipient_value=item.recipient_value,
                    platform_id=item.platform_id,
                    mail_profile_id=(str(item.mail_profile_id) if item.mail_profile_id else None),
                    template_key=item.template_key,
                    enabled=item.enabled,
                    revision=revision.revision + 1,
                    updated_at=now,
                )
                for item in routes
            ]
        )
        revision.revision += 1
        self.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="notification-routes",
                revision=revision.revision,
                changes={
                    "action": "replace",
                    "routes": [
                        {
                            "event_type": item.event_type,
                            "recipient_type": item.recipient_type,
                            "recipient_value": item.recipient_value,
                            "platform_id": item.platform_id,
                            "mail_profile_id": (
                                str(item.mail_profile_id) if item.mail_profile_id else None
                            ),
                            "template_key": item.template_key,
                            "enabled": item.enabled,
                        }
                        for item in routes
                    ],
                },
            )
        )
        await self.session.commit()
        return await self.state()

    async def save_template(
        self, payload: SaveNotificationTemplate, actor_id: UUID
    ) -> NotificationState:
        revision = await self.revision(locked=True)
        if revision.revision != payload.expected_revision:
            raise AuthFailure(409, 40973, "通知模板已在其他窗口修改，请重新读取")
        site = await self.session.get(SiteSettings, 1)
        entry = event_registry(site.data if site else {}).get(payload.event_type)
        if entry is None:
            raise AuthFailure(422, 42299, "未知通知事件")
        definition = entry.definition
        if not definition.accepts_platform(payload.platform_id):
            raise AuthFailure(422, 42299, "此通知事件仅支持邮件")
        if definition.requires_code and not payload.reset:

            def includes_code(value: str) -> bool:
                return any(match.group(1) == "verification.code" for match in TOKEN.finditer(value))

            html_tags = re.findall(r"<[^>]*>", payload.html)
            if (
                not includes_code(payload.text)
                or includes_code(payload.subject)
                or any(includes_code(tag) for tag in html_tags)
                or (payload.html_enabled and not includes_code(sanitize_email_html(payload.html)))
            ):
                raise AuthFailure(422, 42299, "正文必须包含验证码，主题和 HTML 属性不能包含验证码")
        if payload.platform_id not in {item.id for item in await self.platforms()}:
            raise AuthFailure(422, 42299, "通知平台不存在")
        if payload.html.strip() and payload.platform_id != EMAIL:
            raise AuthFailure(422, 42299, "只有邮件平台支持 HTML 正文")
        body_html = payload.html.strip() if payload.platform_id == EMAIL else ""
        html_enabled = bool(payload.html_enabled and payload.platform_id == EMAIL)
        row = await self.session.scalar(
            select(NotificationTemplate)
            .where(
                NotificationTemplate.event_type == payload.event_type,
                NotificationTemplate.platform_id == payload.platform_id,
                NotificationTemplate.locale == payload.locale,
            )
            .with_for_update()
        )
        if payload.reset:
            if row:
                await self.session.delete(row)
        elif row is None:
            row = NotificationTemplate(
                id=uuid5(
                    NAMESPACE_URL,
                    "everunfold:notification-template:"
                    f"{payload.event_type}:{payload.platform_id}:{payload.locale}",
                ),
                event_type=payload.event_type,
                platform_id=payload.platform_id,
                locale=payload.locale,
                subject=payload.subject.strip(),
                text=payload.text.strip(),
                html=body_html,
                html_enabled=html_enabled,
                enabled=payload.enabled,
                revision=revision.revision + 1,
                updated_at=datetime.now(UTC),
            )
            self.session.add(row)
        else:
            row.subject = payload.subject.strip()
            row.text = payload.text.strip()
            row.html = body_html
            row.html_enabled = html_enabled
            row.enabled = payload.enabled
            row.revision = revision.revision + 1
            row.updated_at = datetime.now(UTC)
        revision.revision += 1
        self.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="notification-templates",
                revision=revision.revision,
                changes={
                    "action": "reset" if payload.reset else "save",
                    "event_type": payload.event_type,
                    "platform_id": payload.platform_id,
                    "locale": payload.locale,
                    "html_enabled": html_enabled,
                    "enabled": payload.enabled,
                },
            )
        )
        await self.session.commit()
        return await self.state()

    async def save_brand(self, payload: SaveMailBrand, actor_id: UUID) -> NotificationState:
        site = await SettingsService(self.session, self.config).site(locked=True)
        revision = await self.revision(locked=True)
        if revision.revision != payload.expected_revision:
            raise AuthFailure(409, 40973, "邮件品牌设置已在其他窗口修改，请重新读取")
        site.data = {**site.data, BRAND_KEY: payload.brand.model_dump()}
        revision.revision += 1
        self.session.add(
            SettingsAudit(
                user_id=actor_id,
                resource="notification-mail-brand",
                revision=revision.revision,
                changes={"action": "save"},
            )
        )
        await self.session.commit()
        return await self.state()

    async def _template_for(
        self, event_type: str, platform_id: str, locale: str, *, event_title: str = ""
    ) -> tuple[NotificationTemplate | None, str, str, str, bool, str]:
        locales = [locale, locale.split("-", 1)[0], "zh-CN", "en"]
        for candidate in dict.fromkeys(locales):
            row = await self.session.scalar(
                select(NotificationTemplate).where(
                    NotificationTemplate.event_type == event_type,
                    NotificationTemplate.platform_id == platform_id,
                    NotificationTemplate.locale == candidate,
                    NotificationTemplate.enabled.is_(True),
                )
            )
            if row:
                fallback = default_template(
                    event_type, platform_id, candidate, event_title=event_title
                )
                return (
                    row,
                    row.subject or fallback.subject,
                    row.text or fallback.text,
                    row.html,
                    row.html_enabled,
                    fallback.href,
                )
        fallback = default_template(event_type, platform_id, locale, event_title=event_title)
        return (
            None,
            fallback.subject,
            fallback.text,
            fallback.html,
            bool(fallback.html),
            fallback.href,
        )

    async def _recipients(self, route: NotificationRoute, event: NotificationEvent) -> list[User]:
        if route.recipient_type in {"subject", "user"}:
            if route.recipient_type == "subject":
                identity = event.subject_id
            else:
                try:
                    identity = UUID(route.recipient_value)
                except ValueError:
                    return []
            user = await self.session.get(User, identity) if identity else None
            return [user] if user and user.active else []
        active = select(User).where(User.active.is_(True))
        if route.recipient_type == "role":
            return list(
                (await self.session.scalars(active.where(User.role == route.recipient_value))).all()
            )
        if route.recipient_type == "permission":
            return [
                user
                for user in (await self.session.scalars(active)).all()
                if route.recipient_value in user_permissions(user)
            ]
        return []

    async def dispatch_events(self) -> int:
        events = (
            await self.session.scalars(
                select(NotificationEvent)
                .where(NotificationEvent.dispatched_at.is_(None))
                .order_by(NotificationEvent.occurred_at)
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        ).all()
        routes = (
            await self.session.scalars(
                select(NotificationRoute).where(NotificationRoute.enabled.is_(True))
            )
        ).all()
        created = 0
        now = datetime.now(UTC)
        site = await self.session.get(SiteSettings, 1)
        mail_data = site.data if site else {}
        registry = event_registry(mail_data)
        mail_revision, _ = stored_mail_profiles(mail_data)
        brand_variables = None
        for event in events:
            entry = registry.get(event.event_type)
            if (
                entry is None
                or entry.build != event.source_build
                or entry.definition.delivery_mode != "notification"
            ):
                event.dispatched_at = now
                continue
            definition = entry.definition
            event_data = (
                comment_data(event.data, event.resource_id)
                if event.event_type == "comment.pending_review"
                else event.data
            )
            variables = {
                **event_data,
                "event": {
                    "id": str(event.id),
                    "title": definition.title,
                    "occurred_at": event.occurred_at.isoformat(),
                },
            }
            event_routes = [route for route in routes if route.event_type == event.event_type]
            targets: dict[tuple[str, UUID], NotificationRoute] = {}
            for route in event_routes:
                if not definition.accepts_recipient(
                    route.recipient_type, route.recipient_value
                ) or not definition.accepts_platform(route.platform_id):
                    continue
                for user in await self._recipients(route, event):
                    targets.setdefault((route.platform_id, user.id), route)
            for (platform_id, user_id), route in sorted(targets.items()):
                target_user = await self.session.get(User, user_id)
                if target_user is None or not target_user.active:
                    continue
                if (
                    platform_id == EMAIL
                    and definition.recipient_mode == "subject"
                    and not subject_address_current(event, target_user)
                ):
                    continue
                template, subject, text, html_text, html_enabled, href = await self._template_for(
                    event.event_type,
                    platform_id,
                    target_user.preferred_locale,
                    event_title=definition.title,
                )
                href = render_template(href, variables)
                values = variables
                if platform_id == EMAIL:
                    if brand_variables is None:
                        brand_variables = await site_variables(
                            self.session, self.config, mail_data, stored_brand(mail_data)
                        )
                    values = {
                        **email_urls(variables, brand_variables["url"]),
                        "site": brand_variables,
                        "recipient": {"display_name": target_user.display_name},
                        "mail": {
                            "reason": mail_reason(
                                event.event_type,
                                template.locale if template else target_user.preferred_locale,
                            )
                        },
                    }
                subject = header_value(render_template(subject, values))
                text = render_template(text, values)
                rendered_html = (
                    sanitize_email_html(render_html_template(html_text, values))
                    if platform_id == EMAIL and html_enabled and html_text.strip()
                    else ""
                )
                selected_mail_profile = (
                    mail_profile(mail_data, route.mail_profile_id) if platform_id == EMAIL else None
                )
                delivery = NotificationDelivery(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"everunfold:notification-delivery:{event.id}:{target_user.id}:{platform_id}",
                    ),
                    event_id=event.id,
                    user_id=target_user.id,
                    platform_id=platform_id,
                    mail_profile_id=(
                        str(selected_mail_profile.id)
                        if selected_mail_profile
                        else route.mail_profile_id
                    ),
                    mail_config_revision=(mail_revision if platform_id == EMAIL else None),
                    template_id=template.id if template else None,
                    locale=target_user.preferred_locale,
                    subject=subject,
                    text=text,
                    html=rendered_html,
                    href=href,
                    destination=target_user.email if platform_id == EMAIL else "",
                    status="pending",
                    attempts=0,
                    next_attempt_at=now,
                    created_at=now,
                )
                self.session.add(delivery)
                if platform_id == IN_APP:
                    await self.session.execute(
                        insert(NotificationInbox)
                        .values(
                            id=uuid5(
                                NAMESPACE_URL,
                                f"everunfold:notification-inbox:{event.id}:{target_user.id}",
                            ),
                            event_id=event.id,
                            user_id=target_user.id,
                            title=subject,
                            text=text,
                            href=href,
                            created_at=now,
                        )
                        .on_conflict_do_nothing(),
                    )
                    delivery.status, delivery.finished_at = "sent", now
                created += 1
            event.dispatched_at = now
        await self.session.commit()
        return created

    async def inbox(self, user_id: UUID, limit: int = 50) -> InboxState:
        rows = (
            await self.session.scalars(
                select(NotificationInbox)
                .where(NotificationInbox.user_id == user_id)
                .order_by(NotificationInbox.created_at.desc())
                .limit(max(1, min(100, limit)))
            )
        ).all()
        unread = (
            await self.session.scalar(
                select(func.count(NotificationInbox.id)).where(
                    NotificationInbox.user_id == user_id,
                    NotificationInbox.read_at.is_(None),
                )
            )
            or 0
        )
        return InboxState(
            unread=unread,
            items=[
                NotificationInboxItem(
                    id=item.id,
                    title=item.title,
                    text=item.text,
                    href=item.href,
                    created_at=item.created_at,
                    read_at=item.read_at,
                )
                for item in rows
            ],
        )

    async def mark_read(self, user_id: UUID, payload: MarkNotificationsRead) -> InboxState:
        statement = select(NotificationInbox).where(NotificationInbox.user_id == user_id)
        if payload.ids:
            statement = statement.where(NotificationInbox.id.in_(payload.ids))
        rows = (await self.session.scalars(statement)).all()
        now = datetime.now(UTC)
        for row in rows:
            row.read_at = row.read_at or now
        await self.session.commit()
        return await self.inbox(user_id)
