"""Trusted backend registrations. Database operations cannot edit these sources."""

import uuid

from app.modules.shortcodes.compiler import compile_shortcode
from app.modules.shortcodes.schemas import ShortcodeItem, ShortcodeParameter, ShortcodeSource


def builtin_catalog() -> list[ShortcodeItem]:
    sources = [
        ShortcodeSource(
            name="notice",
            title="提示框",
            description="提示、成功、警告或错误信息。",
            parameters=[
                ShortcodeParameter(name="title", label="标题", default="提示"),
                ShortcodeParameter(
                    name="type",
                    label="类型",
                    type="select",
                    default="info",
                    options=["info", "success", "warning", "error"],
                ),
            ],
            html=(
                '<aside class="notice" data-variant="{{type}}"><strong>{{title}}</strong>'
                "<div>{{content}}</div></aside>"
            ),
            css=(
                ".notice{padding:1em 1.25em;border-radius:8px;"
                "border-left:4px solid #5b7bcc;background:#5b7bcc14}"
                '.notice[data-variant="success"]{border-color:#24966b;background:#24966b14}'
                '.notice[data-variant="warning"]{border-color:#c18a18;background:#c18a1814}'
                '.notice[data-variant="error"]{border-color:#c95151;background:#c9515114}'
                ".notice > strong{display:block;margin-bottom:.4em}"
                ".notice p:last-child{margin-bottom:0}"
            ),
        ),
        ShortcodeSource(
            name="accordion",
            title="折叠内容",
            description="可由读者展开的详细说明。",
            parameters=[ShortcodeParameter(name="title", label="标题", default="展开查看")],
            html=(
                '<details class="accordion"><summary>{{title}}</summary>'
                '<div class="body">{{content}}</div></details>'
            ),
            css=(
                ".accordion{border:1px solid #8885;border-radius:8px;padding:.8em 1em}"
                ".accordion > summary{cursor:pointer;font-weight:600}"
                ".body{padding-top:.8em}.body p:last-child{margin-bottom:0}"
            ),
        ),
    ]
    return [
        ShortcodeItem(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, "everunfold:shortcode:" + source.name)),
            origin="backend",
            editable=False,
            revision=1,
            applied_revision=1,
            archived=False,
            source=source,
            compiled=compile_shortcode(
                source,
                str(uuid.uuid5(uuid.NAMESPACE_URL, "everunfold:shortcode:" + source.name)),
                1,
            ),
        )
        for source in sources
    ]
