# ruff: noqa: E501
"""Code-owned notification events and safe built-in template fallbacks."""

from dataclasses import dataclass
from textwrap import dedent

from app.modules.notifications.definitions import NotificationEventDefinition as EventDefinition
from app.modules.notifications.email_layout import button, email_layout


@dataclass(frozen=True)
class DefaultTemplate:
    subject: str
    text: str
    html: str = ""
    href: str = ""


def alert_email(
    eyebrow: str,
    title: str,
    body: str,
    action_label: str,
    action_url: str,
    locale: str,
) -> str:
    return email_layout(
        eyebrow,
        title,
        f'<p style="margin:0;font-size:15px;line-height:1.7">{body}</p>',
        button(action_label, action_url),
        locale,
    )


EMAIL_VERIFICATION = "security.email_verification"
EMAIL_VERIFIED = "security.email_verified"
REGISTRATION_VERIFICATION = "security.registration_verification"
PASSWORD_RECOVERY = "security.password_recovery"
PASSWORD_RESET_COMPLETED = "security.password_reset_completed"
TWO_FACTOR_ENABLED = "security.two_factor_enabled"
TWO_FACTOR_DISABLED = "security.two_factor_disabled"
TWO_FACTOR_RECOVERY_ROTATED = "security.two_factor_recovery_rotated"
TWO_FACTOR_RECOVERY_USED = "security.two_factor_recovery_used"
REGISTRATION_SUCCEEDED = "account.registration_succeeded"
USER_CREATED = "account.user_created"

VERIFICATION_PURPOSES = {
    EMAIL_VERIFICATION: ("验证邮箱", "Email verification"),
    REGISTRATION_VERIFICATION: ("注册账号", "Account registration"),
    PASSWORD_RECOVERY: ("找回密码", "Password recovery"),
}


def verification_email(locale: str, event: str = EMAIL_VERIFICATION) -> DefaultTemplate:
    zh = locale == "zh-CN"
    titles = {
        EMAIL_VERIFICATION: ("验证你的邮箱", "Verify your email address"),
        REGISTRATION_VERIFICATION: ("验证注册邮箱", "Verify your registration email"),
        PASSWORD_RECOVERY: ("找回账号密码", "Recover your account password"),
    }
    title = titles[event][not zh]
    purpose = "用途：{{verification.purpose}}" if zh else "Purpose: {{verification.purpose}}"
    recovery = event == PASSWORD_RECOVERY
    if recovery:
        lead = (
            "请将以下重置码粘贴到发起找回密码的浏览器中："
            if zh
            else "Paste this reset code into the browser where you requested it:"
        )
    else:
        lead = "请输入以下验证码：" if zh else "Enter this verification code:"
    code_style = (
        "font-size:18px;overflow-wrap:anywhere;word-break:break-all"
        if recovery
        else "font-size:30px;letter-spacing:0.18em"
    )
    note = (
        "验证码 {{verification.expires_minutes}} 分钟内有效，请勿向他人透露。"
        "如果这不是你的操作，可以忽略这封邮件。"
        if zh
        else "This code expires in {{verification.expires_minutes}} minutes. "
        "Keep it private. If you did not request it, you can ignore this email."
    )
    return DefaultTemplate(
        subject=f"[{{{{site.name}}}}] {title}",
        text=f"{purpose}\n\n{lead}\n{{{{verification.code}}}}\n\n{note}",
        html=email_layout(
            "{{verification.purpose}}",
            title,
            dedent(
                f"""\
                <p style="margin:0;font-size:15px">{lead}</p>
                <p style="margin:14px 0;padding:16px;background:#f3f5f9;border-radius:8px;text-align:center;color:#202631;font-family:monospace;{code_style}">
                  <strong>{{{{verification.code}}}}</strong>
                </p>
                <p style="margin:0;font-size:14px;line-height:1.7">{note}</p>"""
            ),
            "",
            locale,
        ),
    )


def password_reset_email(locale: str) -> DefaultTemplate:
    zh = locale == "zh-CN"
    title = "你的密码已重置" if zh else "Your password was reset"
    message = (
        "你的账号密码已重置，旧会话和 API 密钥已撤销。如果这不是你的操作，请联系站点管理员。"
        if zh
        else "Your account password was reset. Previous sessions and API keys were revoked. "
        "If you did not do this, contact the site administrator."
    )
    return DefaultTemplate(
        subject=f"[{{{{site.name}}}}] {title}",
        text=message,
        html=email_layout(title, title, f"<p>{message}</p>", "", locale),
    )


def comment_email(locale: str) -> DefaultTemplate:
    zh = locale == "zh-CN"
    title = "评论待审核" if zh else "Comment awaiting review"
    author = "评论作者" if zh else "Comment by"
    approve = "通过评论" if zh else "Approve comment"
    reject = "拒绝评论" if zh else "Reject comment"
    review = "查看评论" if zh else "Review comment"
    note = (
        "请登录后台，在评论管理中确认审核操作。"
        if zh
        else "Sign in to the dashboard to confirm the moderation action."
    )
    return DefaultTemplate(
        subject=f"[{{{{site.name}}}}] {title} · {{{{resource.title}}}}",
        text=(
            f"{{{{resource.title}}}}\n{author}: {{{{actor.name}}}}\n\n"
            f"{{{{content.excerpt}}}}\n\n{note}\n"
            f"{approve}: {{{{comment.approve_url}}}}\n"
            f"{reject}: {{{{comment.reject_url}}}}\n"
            f"{review}: {{{{resource.review_url}}}}"
        ),
        html=email_layout(
            title,
            "{{resource.title}}",
            dedent(
                f"""\
                <blockquote style="margin:0;padding:14px 16px;background:#f3f5f9;border-radius:8px">
                  <strong>{author} · {{{{actor.name}}}}</strong>
                  <p style="margin:8px 0 0;line-height:1.7">{{{{content.excerpt}}}}</p>
                </blockquote>
                <p style="margin:16px 0 0;font-size:14px;line-height:1.7">{note}</p>"""
            ),
            "\n".join(
                (
                    button(approve, "{{comment.approve_url}}"),
                    button(reject, "{{comment.reject_url}}", secondary=True),
                    button(review, "{{resource.review_url}}", secondary=True),
                )
            ),
            locale,
        ),
        href="{{resource.review_url}}",
    )


EVENTS = (
    EventDefinition(
        id="comment.pending_review",
        title="评论待审核",
        description="读者提交评论并进入审核队列。",
        variables=(
            "actor.name",
            "content.excerpt",
            "resource.title",
            "resource.url",
            "resource.review_url",
            "comment.author_name",
            "comment.excerpt",
            "comment.review_url",
            "comment.approve_url",
            "comment.reject_url",
        ),
    ),
    EventDefinition(
        id="content.scheduled_published",
        title="定时发布成功",
        description="内容已按计划发布到前台。",
        variables=(
            "content.title",
            "content.type",
            "content.url",
            "schedule.planned_at",
            "admin.url",
        ),
    ),
    EventDefinition(
        id="content.schedule_failed",
        title="定时发布失败",
        description="计划发布因权限、路径或内容校验失败而停止。",
        variables=(
            "content.title",
            "content.type",
            "schedule.planned_at",
            "failure.reason",
            "admin.url",
        ),
    ),
    EventDefinition(
        id="settings.delivery_failed",
        title="站点同步失败",
        description="前端节点在最大重试次数后仍未应用设置或内容快照。",
        variables=(
            "node.id",
            "settings.resource",
            "failure.reason",
            "admin.url",
        ),
    ),
)

ACCOUNT_EVENTS = (
    EventDefinition(
        REGISTRATION_SUCCEEDED,
        "注册成功",
        "用户完成邮箱验证并成功注册后通知管理员。",
        ("account.username", "account.display_name", "admin.url"),
        recipient_mode="admins",
    ),
    EventDefinition(
        USER_CREATED,
        "新增用户",
        "管理员在用户管理中创建账号后通知管理员。",
        ("account.username", "account.display_name", "admin.url"),
        recipient_mode="admins",
    ),
)

SECURITY_ALERT_COPY = {
    EMAIL_VERIFIED: (
        ("邮箱已验证", "你的账号已通过邮箱验证。"),
        ("Email address verified", "The email address for your account has been verified."),
    ),
    TWO_FACTOR_ENABLED: (
        ("两步验证已启用", "你的账号已启用两步验证。请妥善保存恢复码。"),
        (
            "Two-factor authentication enabled",
            "Two-factor authentication is now enabled on your account. Keep your recovery codes safe.",
        ),
    ),
    TWO_FACTOR_DISABLED: (
        ("两步验证已关闭", "你的账号已关闭两步验证。"),
        (
            "Two-factor authentication disabled",
            "Two-factor authentication was turned off on your account.",
        ),
    ),
    TWO_FACTOR_RECOVERY_ROTATED: (
        ("两步验证恢复码已更新", "你的账号已重新生成恢复码，原有恢复码已失效。"),
        (
            "Two-factor recovery codes replaced",
            "New recovery codes were generated for your account. The previous codes no longer work.",
        ),
    ),
    TWO_FACTOR_RECOVERY_USED: (
        ("已使用两步验证恢复码登录", "你的账号使用了一个一次性恢复码登录。"),
        (
            "Signed in with a recovery code",
            "A one-time recovery code was used to sign in to your account.",
        ),
    ),
}

SECURITY_ALERT_DESCRIPTIONS = {
    EMAIL_VERIFIED: "邮箱通过验证码验证后，向该邮箱发送确认。",
    TWO_FACTOR_ENABLED: "启用两步验证后，向该账号的已验证邮箱发送提醒。",
    TWO_FACTOR_DISABLED: "关闭两步验证后，向该账号的已验证邮箱发送提醒。",
    TWO_FACTOR_RECOVERY_ROTATED: "重新生成恢复码后，向该账号的已验证邮箱发送提醒。",
    TWO_FACTOR_RECOVERY_USED: "使用恢复码成功登录后，向该账号的已验证邮箱发送提醒。",
}

SECURITY_EVENTS = (
    EventDefinition(
        id=EMAIL_VERIFICATION,
        title="验证邮箱",
        description="向待验证的邮箱发送验证码。",
        variables=("verification.code", "verification.expires_minutes", "verification.purpose"),
        requires_code=True,
        recipient_mode="subject",
        delivery_mode="security_required",
        email_only=True,
    ),
    EventDefinition(
        id=REGISTRATION_VERIFICATION,
        title="注册验证码",
        description="向注册申请填写的邮箱发送验证码。",
        variables=("verification.code", "verification.expires_minutes", "verification.purpose"),
        requires_code=True,
        recipient_mode="subject",
        delivery_mode="security_required",
        email_only=True,
    ),
    EventDefinition(
        id=PASSWORD_RECOVERY,
        title="找回密码验证码",
        description="向已验证邮箱发送一次性密码重置码。",
        variables=("verification.code", "verification.expires_minutes", "verification.purpose"),
        requires_code=True,
        recipient_mode="subject",
        delivery_mode="security_required",
        email_only=True,
    ),
    EventDefinition(
        id=PASSWORD_RESET_COMPLETED,
        title="密码已重置",
        description="自助找回密码成功后发送安全提醒。",
        recipient_mode="subject",
        delivery_mode="security_optional",
        email_only=True,
    ),
    *(
        EventDefinition(
            identity,
            copy[0][0],
            SECURITY_ALERT_DESCRIPTIONS[identity],
            ("account.security_url",),
            recipient_mode="subject",
            email_only=True,
        )
        for identity, copy in SECURITY_ALERT_COPY.items()
    ),
)

BUILTIN_EVENTS = (*SECURITY_EVENTS, *ACCOUNT_EVENTS, *EVENTS)
BUILTIN_BY_ID = {event.id: event for event in BUILTIN_EVENTS}
SECURITY_EVENT_IDS = frozenset(event.id for event in SECURITY_EVENTS)


def account_template(event: str, platform: str, locale: str) -> DefaultTemplate:
    english = locale == "en"
    if event == REGISTRATION_SUCCEEDED:
        title = "New account registered" if english else "注册成功"
        body = (
            "{{account.display_name}} ({{account.username}}) verified their email and registered."
            if english
            else "「{{account.display_name}}」（{{account.username}}）已完成邮箱验证并注册。"
        )
    else:
        title = "New user added" if english else "新增用户"
        body = (
            "{{account.display_name}} ({{account.username}}) was added in user management."
            if english
            else "已在用户管理中创建「{{account.display_name}}」（{{account.username}}）。"
        )
    action = "Review account" if english else "查看账号"
    return DefaultTemplate(
        subject=f"{title} · {{{{account.username}}}}",
        text=f"{body}\n\n{action}: {{{{admin.url}}}}",
        html=alert_email(title, title, body, action, "{{admin.url}}", locale)
        if platform == "email"
        else "",
        href="{{admin.url}}",
    )


def security_alert_template(event: str, locale: str) -> DefaultTemplate:
    en = locale == "en"
    title, body = SECURITY_ALERT_COPY[event][en]
    action = "Open account settings" if en else "打开账号设置"
    advice = (
        "If you did not make this change, review your sessions and secure your account."
        if en
        else "如果这不是你的操作，请检查登录会话并保护账号。"
    )
    return DefaultTemplate(
        subject=f"{{{{site.name}}}} · {title}",
        text=f"{body}\n{advice}\n\n{action}: {{{{account.security_url}}}}",
        html=alert_email(
            "ACCOUNT SECURITY" if en else "账号安全",
            title,
            f"{body}<br>{advice}",
            action,
            "{{account.security_url}}",
            locale,
        ),
        href="{{account.security_url}}",
    )


DEFAULT_TEMPLATES = {
    **{
        event: {"email": {locale: verification_email(locale, event) for locale in ("zh-CN", "en")}}
        for event in VERIFICATION_PURPOSES
    },
    PASSWORD_RESET_COMPLETED: {
        "email": {locale: password_reset_email(locale) for locale in ("zh-CN", "en")}
    },
    **{
        event.id: {
            platform: {
                locale: account_template(event.id, platform, locale) for locale in ("zh-CN", "en")
            }
            for platform in ("email", "in-app")
        }
        for event in ACCOUNT_EVENTS
    },
    **{
        event: {
            "email": {locale: security_alert_template(event, locale) for locale in ("zh-CN", "en")}
        }
        for event in SECURITY_ALERT_COPY
    },
    "comment.pending_review": {
        "in-app": {
            "zh-CN": DefaultTemplate(
                subject="评论待审核",
                text="{{actor.name}} 在「{{resource.title}}」提交了评论：{{content.excerpt}}",
                href="{{resource.review_url}}",
            ),
            "en": DefaultTemplate(
                subject="Comment awaiting review",
                text='{{actor.name}} commented on "{{resource.title}}": {{content.excerpt}}',
                href="{{resource.review_url}}",
            ),
        },
        "email": {"zh-CN": comment_email("zh-CN"), "en": comment_email("en")},
    },
    "content.scheduled_published": {
        "in-app": {
            "zh-CN": DefaultTemplate(
                subject="定时发布成功 · {{content.title}}",
                text=(
                    "「{{content.title}}」已按计划发布到前台。\n\n"
                    "计划时间：{{schedule.planned_at}}\n访问：{{content.url}}"
                ),
                href="{{content.url}}",
            ),
            "en": DefaultTemplate(
                subject="Scheduled publication completed · {{content.title}}",
                text=(
                    '"{{content.title}}" was published exactly as scheduled.\n\n'
                    "Scheduled: {{schedule.planned_at}}\nOpen: {{content.url}}"
                ),
                href="{{content.url}}",
            ),
        },
        "email": {
            "zh-CN": DefaultTemplate(
                subject="定时发布成功 · {{content.title}}",
                text=(
                    "{{content.type}}「{{content.title}}」已按计划发布。\n\n"
                    "计划时间：{{schedule.planned_at}}\n访问：{{content.url}}"
                ),
                html=alert_email(
                    "定时发布",
                    "{{content.title}}",
                    "内容已按计划发布。<br>计划时间：{{schedule.planned_at}}",
                    "查看内容 →",
                    "{{content.url}}",
                    "zh-CN",
                ),
                href="{{content.url}}",
            ),
            "en": DefaultTemplate(
                subject="Scheduled publication completed · {{content.title}}",
                text=(
                    '{{content.type}} "{{content.title}}" was published as scheduled.\n\n'
                    "Scheduled: {{schedule.planned_at}}\nOpen: {{content.url}}"
                ),
                html=alert_email(
                    "SCHEDULED PUBLICATION",
                    "{{content.title}}",
                    "The content was published as scheduled.<br>Scheduled: {{schedule.planned_at}}",
                    "Open content →",
                    "{{content.url}}",
                    "en",
                ),
                href="{{content.url}}",
            ),
        },
    },
    "content.schedule_failed": {
        "in-app": {
            "zh-CN": DefaultTemplate(
                subject="定时发布失败 · {{content.title}}",
                text=(
                    "「{{content.title}}」未能按计划发布。\n\n"
                    "计划时间：{{schedule.planned_at}}\n错误代码：{{failure.reason}}\n"
                    "请在后台检查并重新安排。"
                ),
                href="{{admin.url}}",
            ),
            "en": DefaultTemplate(
                subject="Scheduled publication failed · {{content.title}}",
                text=(
                    '"{{content.title}}" could not be published as scheduled.\n\n'
                    "Scheduled: {{schedule.planned_at}}\nError code: {{failure.reason}}\n"
                    "Open the administration to review and reschedule."
                ),
                href="{{admin.url}}",
            ),
        },
        "email": {
            "zh-CN": DefaultTemplate(
                subject="定时发布失败 · {{content.title}}",
                text=(
                    "「{{content.title}}」未能按计划发布。\n\n"
                    "计划时间：{{schedule.planned_at}}\n错误代码：{{failure.reason}}\n"
                    "管理：{{admin.url}}"
                ),
                html=alert_email(
                    "定时发布失败",
                    "{{content.title}}",
                    "计划时间：{{schedule.planned_at}}<br>错误代码：{{failure.reason}}",
                    "检查并重新安排 →",
                    "{{admin.url}}",
                    "zh-CN",
                ),
                href="{{admin.url}}",
            ),
            "en": DefaultTemplate(
                subject="Scheduled publication failed · {{content.title}}",
                text=(
                    '"{{content.title}}" could not be published as scheduled.\n\n'
                    "Scheduled: {{schedule.planned_at}}\nError code: {{failure.reason}}\n"
                    "Review: {{admin.url}}"
                ),
                html=alert_email(
                    "SCHEDULE FAILED",
                    "{{content.title}}",
                    "Scheduled: {{schedule.planned_at}}<br>Error code: {{failure.reason}}",
                    "Review and reschedule →",
                    "{{admin.url}}",
                    "en",
                ),
                href="{{admin.url}}",
            ),
        },
    },
    "settings.delivery_failed": {
        "in-app": {
            "zh-CN": DefaultTemplate(
                subject="站点同步失败 · {{node.id}}",
                text=(
                    "{{settings.resource}} 在节点 {{node.id}} 上持续同步失败。\n\n"
                    "原因：{{failure.reason}}\n请检查接收器、节点配置和运行状态。"
                ),
                href="{{admin.url}}",
            ),
            "en": DefaultTemplate(
                subject="Site sync failed · {{node.id}}",
                text=(
                    "{{settings.resource}} could not be applied to node {{node.id}}.\n\n"
                    "Reason: {{failure.reason}}\n"
                    "Check the receiver, node configuration and runtime."
                ),
                href="{{admin.url}}",
            ),
        },
        "email": {
            "zh-CN": DefaultTemplate(
                subject="站点同步失败 · {{node.id}}",
                text=(
                    "{{settings.resource}} 在节点 {{node.id}} 上持续同步失败。\n\n"
                    "原因：{{failure.reason}}\n管理：{{admin.url}}"
                ),
                html=alert_email(
                    "站点同步失败",
                    "{{node.id}}",
                    "资源：{{settings.resource}}<br>原因：{{failure.reason}}",
                    "检查站点同步 →",
                    "{{admin.url}}",
                    "zh-CN",
                ),
                href="{{admin.url}}",
            ),
            "en": DefaultTemplate(
                subject="Site sync failed · {{node.id}}",
                text=(
                    "{{settings.resource}} could not be applied to node {{node.id}}.\n\n"
                    "Reason: {{failure.reason}}\nReview: {{admin.url}}"
                ),
                html=alert_email(
                    "SITE SYNC FAILED",
                    "{{node.id}}",
                    "Resource: {{settings.resource}}<br>Reason: {{failure.reason}}",
                    "Review site sync →",
                    "{{admin.url}}",
                    "en",
                ),
                href="{{admin.url}}",
            ),
        },
    },
}


def default_template(
    event_type: str, platform_id: str, locale: str, *, event_title: str = ""
) -> DefaultTemplate:
    variants = DEFAULT_TEMPLATES.get(event_type, {}).get(platform_id, {})
    return (
        variants.get(locale)
        or variants.get(locale.split("-", 1)[0])
        or variants.get("zh-CN")
        or DefaultTemplate(subject=event_title or event_type, text=event_title or event_type)
    )
