from app.plugin_sdk import PluginRegistry, define_plugin

from .captcha import provider


def setup(registry: PluginRegistry) -> None:
    registry.captcha.register(provider)


plugin = define_plugin(
    id="core.captcha-turnstile",
    preview="asset:assets/preview.webp",
    title="Cloudflare 验证码",
    description="Cloudflare Turnstile 服务端校验，配合前端验证码组件使用。",
    author="Everunfold",
    setup=setup,
)
