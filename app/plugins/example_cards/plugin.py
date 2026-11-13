"""A complete single-file plugin: two templates and namespaced translations."""

from app.plugin_sdk import Plugin, PluginRegistry, ShortcodeParameter, ShortcodeSource


class CardsPlugin(Plugin):
    id = "example.cards"
    preview = "asset:assets/preview.webp"
    title = "示例卡片"
    version = "1.0.0"
    author = "Everunfold"
    description = "单文件插件示例：正文卡片与行内标签，使用主题文字和边框颜色。"

    def setup(self, registry: PluginRegistry) -> None:
        registry.shortcodes.register(
            ShortcodeSource(
                name="example:card",
                title="示例卡片",
                description="带可选标题的正文卡片，可嵌套 Markdown 和其他短代码。",
                parameters=[ShortcodeParameter(name="title", label="标题", default="阅读笔记")],
                html=('<section class="card"><h3>{{title}}</h3><div>{{content}}</div></section>'),
                css=(
                    ".card{border:1px solid currentColor;border-radius:12px;padding:1.25em}"
                    ".card > h3{margin:0 0 .75em;font-size:1.05em}"
                    ".card p:last-child{margin-bottom:0}"
                ),
                example_body="在这里记录想法，支持 **强调**、链接和列表。",
            )
        )
        registry.shortcodes.register(
            ShortcodeSource(
                name="example:badge",
                title="示例标签",
                kind="leaf",
                description="不需要结束标记的简短标签。",
                parameters=[ShortcodeParameter(name="text", label="文字", default="笔记")],
                html='<span class="badge">{{text}}</span>',
                css=(
                    ".badge{display:inline-block;border:1px solid currentColor;"
                    "border-radius:20px;padding:.15em .7em;font-size:.85em}"
                ),
                example_body="",
            )
        )
        registry.locales.register("zh-CN", {"card": "示例卡片", "badge": "示例标签"})
        registry.locales.register("en", {"card": "Example card", "badge": "Example badge"})


plugin = CardsPlugin()
