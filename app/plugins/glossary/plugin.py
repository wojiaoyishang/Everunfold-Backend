from app.plugin_sdk import PluginRegistry, define_plugin

from .api import public, read_admin, write_admin


def setup(registry: PluginRegistry) -> None:
    registry.routes.register(public, access="public")
    registry.routes.register(read_admin, access="permission", permission="content:read")
    registry.routes.register(write_admin, access="permission", permission="content:write")


plugin = define_plugin(
    id="everunfold.glossary",
    title="术语表与悬浮提示",
    description="维护术语、别名和说明，并按需在前台文章正文中显示提示。",
    version="1.4.0",
    author="Everunfold",
    setup=setup,
)
