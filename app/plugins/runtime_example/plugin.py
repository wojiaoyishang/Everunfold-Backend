"""An opt-in real Redis subscription owned by Scheduler; no extra service command."""

from app.plugin_sdk import PluginRegistry, TaskDefinition, define_plugin
from app.plugins.runtime_example.parameter_task import ExampleParameters


def setup(registry: PluginRegistry) -> None:
    registry.tasks.register(
        TaskDefinition(
            id="example.runtime.parameters",
            title="Worker 参数示例",
            handler="app.plugins.runtime_example.parameter_task:calculate",
            parameters=ExampleParameters,
            default_parameters={"count": 2},
            manual=True,
            enabled=False,
        )
    )
    registry.tasks.register(
        TaskDefinition(
            id="example.runtime.subscription",
            title="Redis 订阅示例",
            handler="app.plugins.runtime_example.subscription:listen",
            kind="resident",
            interval=15,
        )
    )


plugin = define_plugin(
    id="example.runtime",
    preview="asset:assets/preview.webp",
    title="常驻任务示例",
    description="由 Scheduler 子进程维护 Redis 订阅；默认不启动，可在运行与任务中启用。",
    setup=setup,
)
