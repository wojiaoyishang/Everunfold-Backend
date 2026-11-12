# ruff: noqa: E501
"""Shared presentation for built-in transactional emails."""

from textwrap import dedent


def button(label: str, url: str, *, secondary: bool = False) -> str:
    path = url.removeprefix("{{").removesuffix("}}")
    colors = (
        "background:#f3f5f9;color:#242b37"
        if secondary
        else "background:{{site.accent_color}};color:{{site.accent_text_color}}"
    )
    return (
        f'<a href="{url}" data-mail-optional="{path}" '
        f'style="display:inline-block;margin:16px 8px 0 0;padding:10px 14px;border-radius:6px;'
        f'font-size:14px;font-weight:600;text-decoration:none;{colors}">{label}</a>'
    )


def email_layout(eyebrow: str, title: str, body: str, actions: str, locale: str) -> str:
    site_label, help_label = ("访问站点", "帮助") if locale == "zh-CN" else ("Visit site", "Help")
    return (
        dedent(
            f"""\
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
          style="background:#f3f5f9;color:#242b37;font-family:Arial,'Microsoft YaHei',sans-serif">
          <tr>
            <td align="center" style="padding:24px 12px">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                style="max-width:600px;background:#fff;border:1px solid #e2e6ee;border-radius:10px">
                <tr>
                  <td style="padding:20px 24px;border-top:4px solid {{{{site.accent_color}}}};border-bottom:1px solid #e2e6ee">
                    <img data-mail-optional="site.icon_url" src="{{{{site.icon_url}}}}" width="32" height="32" alt=""
                      style="display:inline-block;margin-right:9px;vertical-align:middle;border-radius:6px">
                    <strong style="font-size:16px;vertical-align:middle">{{{{site.name}}}}</strong>
                  </td>
                </tr>
                <tr>
                  <td style="padding:22px 24px 24px">
                    <p style="margin:0 0 6px;color:#555f70;font-size:13px">{{{{eyebrow}}}}</p>
                    <h1 style="margin:0 0 14px;color:#1d2531;font-size:23px;line-height:1.35;overflow-wrap:anywhere">{{{{title}}}}</h1>
                    {{{{body}}}}
                    {{{{actions}}}}
                  </td>
                </tr>
                <tr>
                  <td style="padding:14px 24px;border-top:1px solid #e2e6ee;color:#555f70;font-size:12px;line-height:1.6">
                    <p style="margin:0">{{{{mail.reason}}}}</p>
                    <a data-mail-optional="site.url" href="{{{{site.url}}}}" style="margin-right:12px;color:#344258">{site_label}</a>
                    <a data-mail-optional="site.support_url" href="{{{{site.support_url}}}}" style="color:#344258">{help_label}</a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """
        )
        .replace("{{eyebrow}}", eyebrow)
        .replace("{{title}}", title)
        .replace("{{body}}", body.replace("\n", "\n            "))
        .replace("{{actions}}", actions.replace("\n", "\n            "))
    )
