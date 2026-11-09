"""Trusted backend provider discovery. No request-controlled imports or verification URLs."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.modules.captcha.schemas import ProviderDescription, StoredProvider


@dataclass(frozen=True)
class CaptchaProvider:
    id: str
    title: str
    widget: str
    verify: Callable[[str, StoredProvider], Awaitable[bool]]

    def description(self) -> ProviderDescription:
        return ProviderDescription(id=self.id, title=self.title, widget=self.widget)


def provider_catalog() -> dict[str, CaptchaProvider]:
    from app.core.plugins.loader import plugin_catalog

    return {
        provider.id: provider for plugin in plugin_catalog().plugins for provider in plugin.captcha
    }


async def verify_site_token(url: str, answer: str, settings: StoredProvider) -> bool:
    import httpx2 as httpx

    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            response = await client.post(url, data={"secret": settings.secret, "response": answer})
        if response.status_code != 200 or len(response.content) > 16384:
            return False
        data = response.json()
        return (
            isinstance(data, dict)
            and data.get("success") is True
            and isinstance(data.get("hostname"), str)
            and data.get("hostname", "").lower() in settings.hostnames
        )
    except (httpx.HTTPError, ValueError, TypeError):
        from app.modules.auth.service import AuthFailure

        raise AuthFailure(503, 50386, "验证码服务暂不可用，请刷新后重试") from None
