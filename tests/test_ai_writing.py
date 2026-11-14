"""AI transport and data-boundary regressions using synthetic material only."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx2
import pytest
from pydantic import ValidationError

from app.modules.auth.service import AuthFailure
from app.plugins.ai_writing import transport
from app.plugins.ai_writing.safety import reviewed_text
from app.plugins.ai_writing.schemas import GenerateAiText, SaveAiConfiguration
from app.plugins.ai_writing.service import source_for


def test_supported_fields_and_password_region_are_checked() -> None:
    request = GenerateAiText(
        request_id=uuid4(),
        scene="content.article.edit",
        field="seo_description",
        language="zh-CN",
        title="标题",
        summary="预览摘要",
        body=(
            "前面可见。&lbrace;&lbrace;&lt; reply-only &gt;}}隐藏区域{{< /reply-only >}} 后面可见。"
        ),
    )
    assert "前面可见" in source_for(request, False)
    assert "后面可见" in source_for(request, False)
    assert "隐藏区域" not in source_for(request, False)
    assert source_for(request, True) == "标题\n标题"
    assert reviewed_text("公开{{< password-area >}}未闭合") == ""
    assert reviewed_text("公开<script>alert(1)</script>正文") == "公开正文"
    with pytest.raises(AuthFailure):
        source_for(request.model_copy(update={"title": ""}), True)
    with pytest.raises(ValidationError):
        GenerateAiText.model_validate({**request.model_dump(), "field": "canonical"})
    with pytest.raises(ValidationError):
        SaveAiConfiguration(
            revision=0,
            enabled=False,
            base_url="",
            model="",
            fields={"settings.site": ["seo_title"]},
        )


def test_provider_network_policy_needs_explicit_approval_for_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(transport.PROVIDER_ORIGINS_ENV, raising=False)
    url = transport.endpoint("https://127.0.0.1/v1")
    with pytest.raises(transport.ProviderError, match="provider_unavailable"):
        asyncio.run(transport.approved_address(url))
    monkeypatch.setenv(transport.PROVIDER_ORIGINS_ENV, "https://127.0.0.1")
    assert asyncio.run(transport.approved_address(url)) == "127.0.0.1"
    assert str(url) == "https://127.0.0.1/v1/chat/completions"
    with pytest.raises(ValueError, match="Invalid"):
        transport.endpoint("https://name:key@127.0.0.1/v1?debug=1")


def test_model_request_keeps_host_tls_name_and_validates_plain_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx2.Request] = []

    async def address(url: httpx2.URL) -> str:
        assert url.host == "gateway.example.test"
        return "192.0.2.55"

    def mock_handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "<b>一个标题</b>"}}]
            },
        )

    actual_client = httpx2.AsyncClient

    @asynccontextmanager
    async def client(
        *, follow_redirects: bool, trust_env: bool
    ) -> AsyncIterator[httpx2.AsyncClient]:
        assert not follow_redirects and not trust_env
        async with actual_client(transport=httpx2.MockTransport(mock_handler)) as mock:
            yield mock

    monkeypatch.setattr(transport, "approved_address", address)
    monkeypatch.setattr(httpx2, "AsyncClient", client)
    result = asyncio.run(
        transport.generate(
            base_url="https://gateway.example.test/v1",
            model="synthetic",
            key="example-key",
            language="zh-CN",
            task="搜索标题",
            source="仅使用样例输入",
            maximum=10,
            single_line=True,
        )
    )
    assert result == "一个标题"
    assert len(requests) == 1
    request = requests[0]
    assert request.url.host == "192.0.2.55"
    assert request.url.path == "/v1/chat/completions"
    assert request.headers["Host"] == "gateway.example.test"
    assert request.extensions["sni_hostname"] == "gateway.example.test"
    with pytest.raises(transport.ProviderError, match="invalid_output"):
        transport.result_text(
            {"choices": [{"finish_reason": "tool_calls", "message": {"content": "内容"}}]}, 20, True
        )
