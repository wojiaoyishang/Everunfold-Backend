"""Export keeps registration semantics without a cross-container handoff file."""

import asyncio
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import FrontendConfig
from app.modules.settings.models import FrontendNodeRegistry
from app.modules.settings.node_registry import deployed_binding


def test_export_returns_portable_credentials_without_writing_connection_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "deploy"))
    exporter = importlib.import_module("node_bundle")
    codec = importlib.import_module("connection_bundle")
    path = tmp_path / "config.yaml"
    path.touch()
    environment = {"TEST_NODE_IN": "i" * 43, "TEST_NODE_OUT": "o" * 43}
    document = {
        "assistant": {
            "node": "web-main",
            "receiver": "https://frontend.example.test",
            "inbound_env": "TEST_NODE_IN",
            "outbound_env": "TEST_NODE_OUT",
        },
        "environment": environment,
    }
    monkeypatch.setattr(exporter, "configuration_path", lambda: path)
    monkeypatch.setattr(exporter, "read_deployment", lambda _: document)
    remote = FrontendConfig(
        settings_receiver_url="https://remote.example.test",
        nextjs_to_fastapi_token_env="TEST_REMOTE_IN",
        fastapi_to_nextjs_token_env="TEST_REMOTE_OUT",
    )
    environment.update(TEST_REMOTE_IN="a" * 43, TEST_REMOTE_OUT="b" * 43)
    monkeypatch.setattr(
        exporter,
        "load_settings",
        lambda: SimpleNamespace(
            BACKEND_BASE_URL="https://api.example.test",
            database=SimpleNamespace(url=SimpleNamespace(get_secret_value=lambda: "db-url")),
            frontends={"web-remote": remote},
        ),
    )
    session = SimpleNamespace(scalar=AsyncMock(return_value="12345678-1234-4234-8234-123456789abc"))

    @asynccontextmanager
    async def database_session() -> AsyncIterator[SimpleNamespace]:
        yield session

    database = SimpleNamespace(session=database_session, close=AsyncMock())
    database_factory = Mock(return_value=database)
    monkeypatch.setattr(exporter, "Database", database_factory)
    existing = FrontendConfig(
        settings_receiver_url="https://edited-receiver.example.test",
        nextjs_to_fastapi_token_env="TEST_NODE_IN",
        fastapi_to_nextjs_token_env="TEST_NODE_OUT",
    )
    monkeypatch.setattr(
        exporter,
        "registry_record",
        AsyncMock(
            return_value=FrontendNodeRegistry(
                id=1, revision=1, nodes={}, bindings={"web-main": existing.model_dump()}
            )
        ),
    )
    register = AsyncMock()
    monkeypatch.setattr(exporter, "import_nodes", register)
    encoded = asyncio.run(exporter.export())
    decoded = codec.decode_bundle(encoded)
    assert decoded["node_id"] == "web-main"
    assert decoded["tokens"]["inbound"]["value"] == environment["TEST_NODE_IN"]
    assert decoded["tokens"]["outbound"]["value"] == environment["TEST_NODE_OUT"]
    assert decoded["backend_public_url"] == "https://api.example.test"
    assert set(decoded) == {"format", "node_id", "tokens", "source_epoch", "backend_public_url"}
    register.assert_not_awaited()
    database.close.assert_awaited_once()
    assert list(tmp_path.iterdir()) == [path]
    register.reset_mock()
    remote_bundle = codec.decode_bundle(asyncio.run(exporter.export("web-remote")))
    assert remote_bundle["node_id"] == "web-remote"
    assert remote_bundle["tokens"]["inbound"]["value"] == environment["TEST_REMOTE_IN"]
    assert remote_bundle["tokens"]["outbound"]["value"] == environment["TEST_REMOTE_OUT"]
    register.assert_awaited_once_with(
        True, ("web-remote", deployed_binding(remote)), preserve_binding=False
    )
    assert document["assistant"]["node"] == "web-main"
    # Invalid credentials are rejected before contacting the database.
    document["environment"]["TEST_NODE_OUT"] = environment["TEST_NODE_IN"]
    database_factory.reset_mock()
    with pytest.raises(ValueError, match="令牌"):
        asyncio.run(exporter.export())
    database_factory.assert_not_called()
