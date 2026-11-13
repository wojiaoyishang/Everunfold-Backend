"""Existing shortcode names and identities are preserved behind a plugin entry."""

import uuid

from app.plugin_sdk import PluginRegistry, ShortcodeParameter, ShortcodeSource, define_plugin


def setup(registry: PluginRegistry) -> None:
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
    for source in sources:
        registry.shortcodes.register(
            source,
            identity=str(uuid.uuid5(uuid.NAMESPACE_URL, "everunfold:shortcode:" + source.name)),
        )


plugin = define_plugin(
    id="core.basic-shortcodes",
    preview="asset:assets/preview.webp",
    title="基础短代码",
    author="Everunfold",
    description="提示框与折叠内容，保留已有文章的调用和样式。",
    setup=setup,
)
