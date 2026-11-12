# ruff: noqa: E501
"""Code-owned notification events and safe built-in template fallbacks."""

from dataclasses import dataclass
from textwrap import dedent


@dataclass(frozen=True)
class EventDefinition:
    id: str
    title: str
    description: str
    variables: tuple[str, ...]


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
    footer = (
        "此邮件由 Everunfold 自动发送。"
        if locale == "zh-CN"
        else "This message was sent automatically by Everunfold."
    )
    return dedent(
        f"""\
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
          style="margin:0;padding:24px 12px;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#202631">
          <tr>
            <td align="center">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                style="max-width:620px;border:1px solid #e3e6ec;border-radius:8px;background:#ffffff;overflow:hidden">
                <tr>
                  <td style="padding:26px 28px 24px">
                    <p style="margin:0 0 8px;color:#6b7381;font-size:11px;font-weight:600">{eyebrow}</p>
                    <h1 style="margin:0 0 14px;color:#202631;font-size:21px;line-height:1.4">{title}</h1>
                    <p style="margin:0;color:#596273;font-size:14px;line-height:1.75">{body}</p>
                    <a href="{action_url}"
                      style="display:inline-block;margin-top:20px;padding:10px 16px;border-radius:6px;background:#3154d9;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">{action_label}</a>
                  </td>
                </tr>
                <tr>
                  <td style="padding:14px 28px;border-top:1px solid #edf0f3;color:#8a93a3;font-size:11px;line-height:1.6">{footer}</td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """
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

EVENT_BY_ID = {item.id: item for item in EVENTS}

EMAIL_VERIFICATION = "security.email_verification"
SECURITY_TEMPLATES = (
    EventDefinition(
        id=EMAIL_VERIFICATION,
        title="验证邮箱",
        description="向用户发送邮箱验证验证码。",
        variables=("verification.code", "verification.expires_minutes"),
    ),
)

DEFAULT_TEMPLATES = {
    EMAIL_VERIFICATION: {
        "email": {
            "zh-CN": DefaultTemplate(
                subject="验证你的邮箱",
                text="你的邮箱验证码是：{{verification.code}}\n\n验证码 {{verification.expires_minutes}} 分钟内有效。若不是你发起的，请忽略此邮件。",
                html="<p>你的邮箱验证码是：</p><p><strong>{{verification.code}}</strong></p><p>验证码 {{verification.expires_minutes}} 分钟内有效。若不是你发起的，请忽略此邮件。</p>",
            ),
            "en": DefaultTemplate(
                subject="Verify your email address",
                text="Your verification code is: {{verification.code}}\n\nIt expires in {{verification.expires_minutes}} minutes. Ignore this message if you did not request it.",
                html="<p>Your verification code is:</p><p><strong>{{verification.code}}</strong></p><p>It expires in {{verification.expires_minutes}} minutes. Ignore this message if you did not request it.</p>",
            ),
        },
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
        "email": {
            "zh-CN": DefaultTemplate(
                subject="评论待审核 · {{resource.title}}",
                text=(
                    "{{actor.name}} 在「{{resource.title}}」提交了一条待审核评论。\n\n"
                    "{{content.excerpt}}\n\n审核：{{resource.review_url}}"
                ),
                html=dedent(
                    """\
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                      style="margin:0;padding:24px 12px;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#202631">
                      <tr>
                        <td align="center">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                            style="max-width:620px;border:1px solid #e3e6ec;border-radius:8px;background:#ffffff;overflow:hidden">
                            <tr>
                              <td style="padding:26px 28px 8px">
                                <table role="presentation" cellpadding="0" cellspacing="0">
                                  <tr>
                                    <td width="34" height="34" align="center" valign="middle"
                                      style="border-radius:6px;background:#eef2ff;color:#3154d9;font-size:18px;font-weight:700">E</td>
                                    <td style="padding-left:12px;font-size:12px;letter-spacing:1.5px;color:#7b8495">EVERUNFOLD</td>
                                  </tr>
                                </table>
                                <p style="margin:20px 0 6px;color:#6b7381;font-size:11px;font-weight:600">评论待审核</p>
                                <h1 style="margin:0;color:#202631;font-size:21px;line-height:1.4">{{resource.title}}</h1>
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:8px 28px 24px">
                                <div style="margin-top:8px;padding:16px 18px;border-left:3px solid #3154d9;border-radius:0 6px 6px 0;background:#f8f9fb">
                                  <p style="margin:0 0 8px;color:#202631;font-size:14px;font-weight:700">{{actor.name}}</p>
                                  <p style="margin:0;color:#596273;font-size:14px;line-height:1.75">{{content.excerpt}}</p>
                                </div>
                                <a href="{{resource.review_url}}"
                                  style="display:inline-block;margin-top:20px;padding:10px 16px;border-radius:6px;background:#3154d9;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">查看并审核 →</a>
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:14px 28px;border-top:1px solid #edf0f3;color:#8a93a3;font-size:11px;line-height:1.6">此邮件由 Everunfold 评论审核提醒自动发送。</td>
                            </tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                    """
                ),
                href="{{resource.review_url}}",
            ),
            "en": DefaultTemplate(
                subject="Comment awaiting review · {{resource.title}}",
                text=(
                    '{{actor.name}} submitted a comment on "{{resource.title}}".\n\n'
                    "{{content.excerpt}}\n\nReview: {{resource.review_url}}"
                ),
                html=dedent(
                    """\
                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                      style="margin:0;padding:24px 12px;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#202631">
                      <tr>
                        <td align="center">
                          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                            style="max-width:620px;border:1px solid #e3e6ec;border-radius:8px;background:#ffffff;overflow:hidden">
                            <tr>
                              <td style="padding:26px 28px 8px">
                                <table role="presentation" cellpadding="0" cellspacing="0">
                                  <tr>
                                    <td width="34" height="34" align="center" valign="middle"
                                      style="border-radius:6px;background:#eef2ff;color:#3154d9;font-size:18px;font-weight:700">E</td>
                                    <td style="padding-left:12px;font-size:12px;letter-spacing:1.5px;color:#7b8495">EVERUNFOLD</td>
                                  </tr>
                                </table>
                                <p style="margin:20px 0 6px;color:#6b7381;font-size:11px;font-weight:600">COMMENT REVIEW</p>
                                <h1 style="margin:0;color:#202631;font-size:21px;line-height:1.4">{{resource.title}}</h1>
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:8px 28px 24px">
                                <div style="margin-top:8px;padding:16px 18px;border-left:3px solid #3154d9;border-radius:0 6px 6px 0;background:#f8f9fb">
                                  <p style="margin:0 0 8px;color:#202631;font-size:14px;font-weight:700">{{actor.name}}</p>
                                  <p style="margin:0;color:#596273;font-size:14px;line-height:1.75">{{content.excerpt}}</p>
                                </div>
                                <a href="{{resource.review_url}}"
                                  style="display:inline-block;margin-top:20px;padding:10px 16px;border-radius:6px;background:#3154d9;color:#ffffff;font-size:14px;font-weight:600;text-decoration:none">Review comment →</a>
                              </td>
                            </tr>
                            <tr>
                              <td style="padding:14px 28px;border-top:1px solid #edf0f3;color:#8a93a3;font-size:11px;line-height:1.6">This message was sent automatically by Everunfold.</td>
                            </tr>
                          </table>
                        </td>
                      </tr>
                    </table>
                    """
                ),
                href="{{resource.review_url}}",
            ),
        },
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


def default_template(event_type: str, platform_id: str, locale: str) -> DefaultTemplate:
    variants = DEFAULT_TEMPLATES.get(event_type, {}).get(platform_id, {})
    return (
        variants.get(locale)
        or variants.get(locale.split("-", 1)[0])
        or variants.get("zh-CN")
        or DefaultTemplate(subject=event_type, text=event_type, html="")
    )
