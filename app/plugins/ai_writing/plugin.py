"""Built-in, opt-in AI tools for the administration editor."""

from app.plugin_sdk import PluginRegistry, TaskDefinition, define_plugin

from .api import router
from .schemas import PLUGIN_ID, TASK_ID


def setup(registry: PluginRegistry) -> None:
    registry.routes.register(router, access="session")
    registry.tasks.register(
        TaskDefinition(
            TASK_ID,
            "AI 写作请求",
            "app.plugins.ai_writing.worker:deliver",
            interval=3,
            timeout=70,
            enabled=True,
        )
    )


plugin = define_plugin(
    id=PLUGIN_ID,
    title="AI 写作助手",
    description="在后台为摘要、搜索与分享等字段生成待审阅的文字。",
    version="1.0.0",
    author="Everunfold",
    preview="asset:assets/preview.webp",
    setup=setup,
)
