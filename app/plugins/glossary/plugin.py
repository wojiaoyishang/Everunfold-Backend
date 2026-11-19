from app.plugin_sdk import PluginRegistry, define_plugin

from .api import public, read_admin, write_admin


def setup(registry: PluginRegistry) -> None:
    prefix = "/v1/plugins/everunfold.glossary"
    registry.routes.register(public, prefix=prefix, access="public")
    registry.routes.register(
        read_admin, prefix=prefix, access="permission", permission="content:read"
    )
    registry.routes.register(
        write_admin,
        prefix=prefix,
        access="permission",
        permission="content:write",
        media_references=True,
    )


plugin = define_plugin(
    id="everunfold.glossary",
    preview="asset:assets/preview.webp",
    title="术语表与悬浮提示",
    description="维护术语、别名和说明，并按需在前台文章正文中显示提示。",
    version="1.9.0",
    author="Everunfold",
    setup=setup,
)
