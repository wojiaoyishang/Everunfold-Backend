"""Locale negotiation, fallback and concurrent task isolation."""

import asyncio

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.i18n import current_locale, i18n
from app.core.locales import resolve_locale
from app.entrypoints.api import create_app


def test_locale_messages_and_missing_fallback(caplog: object) -> None:
    assert i18n.t("core.success", locale="en-US") == "Request successful"
    assert i18n.t("core.success", locale="zh-CN") == "请求成功"
    assert i18n.t("core.unknown", locale="en") == "core.unknown"
    assert resolve_locale("en;q=0, zh-CN;q=0.5") == "zh-CN"
    assert resolve_locale("zh-CN;q=0.2,en;q=0.9") == "en"
    assert resolve_locale("en;q=invalid,zh-CN") == "zh-CN"
    token = current_locale.set("en")
    try:
        assert i18n.message("请先登录") == "Sign in first."
        assert i18n.message("文章标题等用户文字") == "文章标题等用户文字"
    finally:
        current_locale.reset(token)


def test_concurrent_locale_contexts_are_isolated() -> None:
    async def job(locale: str) -> str:
        token = current_locale.set(locale)  # type: ignore[arg-type]
        try:
            await asyncio.sleep(0)
            return i18n.t("core.success")
        finally:
            current_locale.reset(token)

    async def run() -> list[str]:
        return list(await asyncio.gather(job("en"), job("zh-CN"), job("en")))

    assert asyncio.run(run()) == ["Request successful", "请求成功", "Request successful"]
    assert current_locale.get() == "zh-CN"


def test_api_explicit_locale_and_accept_language_fallback(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get(
            "/health/live", headers={"X-Site-Locale": "en", "Accept-Language": "zh-CN"}
        )
        assert response.json()["msg"] == "Request successful"
        assert response.headers["content-language"] == "en"
        assert "X-Site-Locale" in response.headers["vary"]
        fallback = client.get(
            "/health/live", headers={"X-Site-Locale": "unsupported", "Accept-Language": "en"}
        )
        assert fallback.json()["msg"] == "Request successful"
        extended = client.get(
            "/health/live",
            headers={
                "X-Site-Locale": "fr",
                "X-Site-UI-Locale": "en",
            },
        )
        assert extended.json()["msg"] == "Request successful"
        assert extended.headers["content-language"] == "fr"
        assert "X-Site-UI-Locale" in extended.headers["vary"]
        assert client.get("/health/live").json()["msg"] == "请求成功"
