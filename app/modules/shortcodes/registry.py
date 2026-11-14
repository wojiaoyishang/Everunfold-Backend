"""Read-only system shortcodes and contributions owned by backend plugins."""

from app.modules.shortcodes.schemas import (
    PublicShortcode,
    ShortcodeItem,
    ShortcodeParameter,
    ShortcodeSource,
)


def region_catalog() -> list[ShortcodeItem]:
    items = []
    for name, title, description, scope in (
        (
            "reply-only",
            "回复可见区域",
            "登录后发表评论，审核通过即可查看。",
            "ef-sc-62a28d862fcc4a23aa20103bd8f5efda",
        ),
        (
            "password-area",
            "密码保护区域",
            "访客输入区域口令后查看。",
            "ef-sc-7a5a3122108c420a955cae832ee445c6",
        ),
    ):
        source = ShortcodeSource(
            name=name,
            title=title,
            description=description,
            html="<div>{{content}}</div>",
            parameters=(
                [ShortcodeParameter(name="credential", label="区域口令", required=True)]
                if name == "password-area"
                else []
            ),
        )
        identity = f"core.{name}"
        items.append(
            ShortcodeItem(
                id=identity,
                origin="backend",
                editable=False,
                revision=1,
                applied_revision=1,
                archived=False,
                source=source,
                compiled=PublicShortcode(
                    id=identity,
                    name=name,
                    title=title,
                    description=description,
                    kind=source.kind,
                    parameters=source.parameters,
                    html=source.html,
                    css="",
                    scope=scope,
                    revision=1,
                ),
            )
        )
    return items


def builtin_catalog() -> list[ShortcodeItem]:
    from app.core.plugins.loader import plugin_catalog

    return [
        *region_catalog(),
        *(
            code.model_copy(deep=True)
            for plugin in plugin_catalog().plugins
            for code in plugin.shortcodes
        ),
    ]
