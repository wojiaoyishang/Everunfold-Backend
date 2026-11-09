"""Trusted backend provider discovery. No request-controlled imports or verification URLs."""

import importlib
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.modules.captcha.schemas import ProviderDescription, StoredProvider


@dataclass(frozen=True)
class CaptchaProvider:
    id: str
    title: str
    widget: str
    verify: Callable[[str, StoredProvider], Awaitable[bool]]

    def description(self) -> ProviderDescription:
        return ProviderDescription(id=self.id, title=self.title, widget=self.widget)


@lru_cache(maxsize=1)
def provider_catalog() -> dict[str, CaptchaProvider]:
    result: dict[str, CaptchaProvider] = {}
    root = Path(__file__).resolve().parents[2] / "plugins"
    for path in sorted(root.glob("*/captcha.py")):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", path.parent.name):
            raise RuntimeError("Invalid CAPTCHA plugin directory")
        provider = getattr(
            importlib.import_module(f"app.plugins.{path.parent.name}.captcha"), "provider", None
        )
        if (
            not isinstance(provider, CaptchaProvider)
            or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", provider.id)
            or provider.id == "local"
            or provider.id in result
            or not inspect.iscoroutinefunction(provider.verify)
        ):
            raise RuntimeError("Invalid or duplicate CAPTCHA provider")
        result[provider.id] = provider
    return result


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
