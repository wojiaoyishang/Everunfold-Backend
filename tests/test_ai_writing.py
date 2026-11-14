"""AI transport and data-boundary regressions using synthetic material only."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from pydantic import ValidationError

from app.core.config import load_settings
from app.core.database import Database
from app.modules.auth.service import AuthFailure
from app.modules.settings.backend_plugins import install_backend_plugins
from app.plugins.ai_writing import transport, worker
from app.plugins.ai_writing.safety import cipher, reviewed_text
from app.plugins.ai_writing.schemas import GenerateAiText, SaveAiConfiguration, SaveAiProvider
from app.plugins.ai_writing.service import source_for, store_provider
from tests.test_accounts import AccountsTest, headers
from tests.test_page_comments import comments as database_fixture

ai_database = database_fixture


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
            fields={"settings.site": ["seo_title"]},
            default_provider_id=None,
            providers=[],
        )


def test_provider_list_requires_unique_names_and_a_valid_default() -> None:
    provider = SaveAiProvider(
        id=uuid4(), name="OpenAI", base_url="https://api.example.test/v1", model="test-model"
    )
    configuration = SaveAiConfiguration(
        revision=0, enabled=True, fields={}, default_provider_id=provider.id, providers=[provider]
    )

    invalid_changes: tuple[dict[str, object], ...] = (
        {"default_provider_id": None},
        {"default_provider_id": uuid4()},
        {"providers": []},
        {"providers": [provider, provider]},
        {"providers": [provider, provider.model_copy(update={"id": uuid4(), "name": "openai"})]},
    )
    for change in invalid_changes:
        with pytest.raises(ValidationError):
            SaveAiConfiguration.model_validate(configuration.model_dump() | change)


def test_provider_keys_keep_their_provider_and_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings("config.example.toml")
    monkeypatch.setenv(settings.auth.application_master_key_env, "test_" + "a" * 43)
    payload = {
        "id": uuid4(),
        "name": "模型服务",
        "base_url": "https://gateway.example.test/v1",
        "model": "model-a",
    }
    provider = SaveAiProvider.model_validate({**payload, "api_key": "synthetic-a-key"})
    stored = store_provider(settings, provider, None)
    assert stored["key_ciphertext"] is not None
    assert cipher(settings, "key").decrypt(stored["key_ciphertext"].encode()) == b"synthetic-a-key"

    retain = SaveAiProvider.model_validate(
        {**payload, "base_url": "https://gateway.example.test/v2"}
    )
    assert store_provider(settings, retain, stored)["key_ciphertext"] == stored["key_ciphertext"]
    assert store_provider(settings, retain, None)["key_ciphertext"] is None
    changed = SaveAiProvider.model_validate(
        {**payload, "base_url": "https://other.example.test/v1"}
    )
    with pytest.raises(AuthFailure, match="更换接口来源"):
        store_provider(settings, changed, stored)
    cleared = changed.model_copy(update={"clear_api_key": True})
    assert store_provider(settings, cleared, stored)["key_ciphertext"] is None
    insecure = provider.model_copy(update={"base_url": "http://gateway.example.test/v1"})
    with pytest.raises(AuthFailure, match="HTTPS"):
        store_provider(settings, insecure, None)


def test_generation_and_connection_use_the_selected_saved_provider(
    ai_database: AccountsTest, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, client = ai_database.settings, ai_database.root
    monkeypatch.setenv(config.auth.application_master_key_env, "test_" + "g" * 43)
    sent: list[dict[str, Any]] = []

    async def record_model_call(**arguments: Any) -> str:
        sent.append(arguments)
        return "样例"

    monkeypatch.setattr(worker, "generate", record_model_call)

    async def install() -> None:
        database = Database(config.database.url.get_secret_value())
        try:
            async with database.session() as session:
                await install_backend_plugins(session, config)
        finally:
            await database.close()

    asyncio.run(install())
    path = "/v1/plugins/core.ai-writing"
    auth = headers(client)
    first, second = str(uuid4()), str(uuid4())
    providers = [
        {
            "id": first,
            "name": "首选服务",
            "base_url": "https://gateway-a.example.test/v1",
            "model": "model-a",
            "api_key": "synthetic-a-key",
        },
        {
            "id": second,
            "name": "备用服务",
            "base_url": "https://gateway-b.example.test/v1",
            "model": "model-b",
            "api_key": "synthetic-b-key",
        },
    ]
    configuration = {
        "revision": client.get(f"{path}/configuration").json()["data"]["revision"],
        "enabled": True,
        "fields": {"content.article.edit": ["excerpt"]},
        "default_provider_id": first,
        "providers": providers,
    }
    saved = client.put(f"{path}/configuration", json=configuration, headers=auth)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["providers"][1]["api_key_configured"]
    assert "synthetic-" not in saved.text and "key_ciphertext" not in saved.text
    revision = saved.json()["data"]["revision"]
    found = client.get(f"{path}/availability").json()["data"]
    assert found["default_provider_id"] == first
    assert found["providers"] == [
        {"id": row["id"], "name": row["name"], "model": row["model"]} for row in providers
    ]

    payload = {
        "request_id": str(uuid4()),
        "provider_id": second,
        "expected_revision": revision,
        "scene": "content.article.edit",
        "field": "excerpt",
        "language": "zh-CN",
        "title": "独立网关测试",
    }
    assert (
        client.post(
            f"{path}/generations", json={**payload, "provider_id": str(uuid4())}, headers=auth
        ).status_code
        == 422
    )
    response = client.post(f"{path}/generations", json=payload, headers=auth)
    assert response.status_code == 200, response.text
    assert (
        client.post(
            f"{path}/generations", json={**payload, "provider_id": first}, headers=auth
        ).status_code
        == 409
    )
    assert asyncio.run(worker.deliver(config)) == {"processed": 1}
    result = client.get(f"{path}/generations/{payload['request_id']}").json()["data"]
    assert result["status"] == "succeeded"
    assert sent[0]["base_url"] == providers[1]["base_url"]
    assert sent[0]["model"] == providers[1]["model"]
    assert sent[0]["key"] == providers[1]["api_key"]

    check = {"request_id": str(uuid4()), "provider_id": first, "expected_revision": revision}
    assert client.post(f"{path}/test", json=check, headers=auth).status_code == 200
    asyncio.run(worker.deliver(config))
    assert sent[1]["base_url"] == providers[0]["base_url"]
    assert sent[1]["model"] == providers[0]["model"]
    assert sent[1]["key"] == providers[0]["api_key"]
    assert sent[1]["source"] == "Connection check."

    request_id = str(uuid4())
    assert (
        client.post(
            f"{path}/generations", json={**payload, "request_id": request_id}, headers=auth
        ).status_code
        == 200
    )
    configuration.update(
        revision=revision,
        providers=[{key: value for key, value in providers[0].items() if key != "api_key"}],
    )
    assert client.put(f"{path}/configuration", json=configuration, headers=auth).status_code == 200
    assert (
        client.post(
            f"{path}/test", json={**check, "request_id": str(uuid4())}, headers=auth
        ).status_code
        == 409
    )
    assert asyncio.run(worker.deliver(config)) == {"processed": 1}
    assert len(sent) == 2
    assert (
        client.get(f"{path}/generations/{request_id}").json()["data"]["error"]
        == "configuration_changed"
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
