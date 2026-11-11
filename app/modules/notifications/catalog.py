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
)

EVENT_BY_ID = {item.id: item for item in EVENTS}

DEFAULT_TEMPLATES = {
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
}


def default_template(event_type: str, platform_id: str, locale: str) -> DefaultTemplate:
    variants = DEFAULT_TEMPLATES.get(event_type, {}).get(platform_id, {})
    return (
        variants.get(locale)
        or variants.get(locale.split("-", 1)[0])
        or variants.get("zh-CN")
        or DefaultTemplate(subject=event_type, text=event_type, html="")
    )
