from app.plugin_sdk import PluginRegistry, define_plugin

from .api import account, protected, public


def setup(registry: PluginRegistry) -> None:
    registry.routes.register(public, access="public")
    registry.routes.register(account)
    registry.routes.register(protected, access="permission", permission="settings:read")


plugin = define_plugin(
    id="example.routes",
    preview="asset:assets/preview.webp",
    title="HTTP 路由示例",
    description="公开问候与回显、登录会话及权限保护的原生 FastAPI 路由。",
    setup=setup,
    readme="在插件的 setup 中调用 routes.register；默认保护为登录会话。"
    "显式公开的示例仅返回静态问候或回显，不修改内容。"
    "路径统一为 /v1/plugins/example.routes/，停用插件立即停止新请求。",
)
