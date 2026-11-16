"""The root document must boot without an old /data/config.toml."""

import os
from pathlib import Path

import pytest
import yaml

from app.core.config import ConfigurationError, load_settings
from app.core.deployment_config import DeploymentConfigError, read_deployment
from app.modules.settings.node_registry import deployment_directory, registration_bindings
from deploy import container_entry


def write_root(root: Path) -> Path:
    path = root / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "component": "backend",
                "application": {
                    "BACKEND_BASE_URL": "https://api.example.test",
                    "project": {"key": "test-root"},
                    "redis": {"namespace_scope": "root-config"},
                },
                "environment": {
                    "DATABASE_URL": "postgresql+asyncpg://test:private-password@127.0.0.1/app",
                    "REDIS_URL": "redis://127.0.0.1:6379/0",
                    "WORKER_CONCURRENCY": "3",
                    "APPLICATION_SECRET": "literal-$$-'quotes'",
                },
            }
        )
    )
    return path


def test_root_yaml_takes_precedence_over_legacy_and_keeps_data_separate(tmp_path: Path) -> None:
    write_root(tmp_path)
    (tmp_path / "config.toml").write_text("legacy is invalid")
    data = tmp_path / "external-data"
    settings = load_settings(project_root=tmp_path, environ={"EVERUNFOLD_DATA_DIR": str(data)})
    assert settings.project.key == "test-root"
    assert settings.media.uploads_path == str(data / "uploads")
    assert not data.exists()
    assert "private-password" not in repr(settings)
    assert "private-password" not in settings.model_dump_json()


def test_explicit_environment_still_overrides_root_document(tmp_path: Path) -> None:
    path = write_root(tmp_path)
    replacement = "postgresql+asyncpg://test:replacement@127.0.0.1/other"
    settings = load_settings(path, environ={"DATABASE_URL": replacement}, project_root=tmp_path)
    assert settings.database.url.get_secret_value() == replacement
    assert settings.media.uploads_path == str(tmp_path / "var/instance/uploads")
    with pytest.raises(ConfigurationError, match="DATABASE_URL cannot be empty"):
        load_settings(path, environ={"DATABASE_URL": ""})


def test_relative_upload_directory_is_under_data(tmp_path: Path) -> None:
    path = write_root(tmp_path)
    document = read_deployment(path)
    document["application"]["media"] = {"uploads_path": "media"}
    path.write_text(yaml.safe_dump(document))
    settings = load_settings(path, environ={"EVERUNFOLD_DATA_DIR": "/data"})
    assert settings.media.uploads_path == "/data/media"


def test_setup_pairing_can_be_reused_only_by_explicit_console_registration(tmp_path: Path) -> None:
    path = write_root(tmp_path)
    document = read_deployment(path)
    document["assistant"] = {
        "node": "web-main",
        "receiver": "http://everunfold-frontend:3000",
        "allow_private_http": True,
        "inbound_env": "TEST_FRONTEND_INBOUND",
        "outbound_env": "TEST_FRONTEND_OUTBOUND",
    }
    document["environment"].update(TEST_FRONTEND_INBOUND="i" * 43, TEST_FRONTEND_OUTBOUND="o" * 43)
    path.write_text(yaml.safe_dump(document))
    settings = load_settings(path, environ={})
    directory = deployment_directory(settings)
    binding = registration_bindings(directory, settings)["web-main"]
    assert binding.settings_receiver_url == "http://everunfold-frontend:3000"
    assert binding.nextjs_to_fastapi_token_env == "TEST_FRONTEND_INBOUND"
    assert binding.fastapi_to_nextjs_token_env == "TEST_FRONTEND_OUTBOUND"
    assert not directory.entries and not directory.targets
    assert "TEST_FRONTEND_INBOUND" not in settings.model_dump_json()
    # An existing registered binding remains authoritative.
    existing = binding.model_copy(update={"settings_receiver_url": "https://existing.example.test"})
    directory.bindings["web-main"] = existing
    assert registration_bindings(directory, settings)["web-main"] == existing
    incomplete = load_settings(path, environ={"TEST_FRONTEND_OUTBOUND": ""})
    assert incomplete.setup_frontend is None


def test_docker_gateway_grant_is_an_exact_proxy_address_and_not_an_internal_grant(
    tmp_path: Path,
) -> None:
    path = write_root(tmp_path)
    document = read_deployment(path)
    document["application"]["nodes_monitor"] = {"trusted_tls_proxy_ips": ["192.0.2.10"]}
    document["application"]["internal_auth"] = {"allowed_peer_ips": ["127.0.0.1"]}
    path.write_text(yaml.safe_dump(document))
    settings = load_settings(
        path,
        environ={
            "EVERUNFOLD_NODES_TLS_PROXY_IPS": "172.18.0.1,192.0.2.10,fd00::1,",
            "EVERUNFOLD_INTERNAL_PEER_CIDRS": "172.19.0.0/16,",
        },
    )
    assert settings.nodes_monitor.trusted_tls_proxy_ips == ["192.0.2.10", "172.18.0.1", "fd00::1"]
    assert settings.internal_auth.allowed_peer_ips == ["127.0.0.1", "172.19.0.0/16"]
    with pytest.raises(ConfigurationError, match="EVERUNFOLD_NODES_TLS_PROXY_IPS"):
        load_settings(path, environ={"EVERUNFOLD_NODES_TLS_PROXY_IPS": "172.18.0.0/16"})


@pytest.mark.parametrize(
    "invalid",
    [
        "version: 1\nversion: 2\n",
        "version: 1\ncomponent: frontend\napplication: {}\n",
        "version: 1\ncomponent: backend\napplication: {}\nenvironment: {TOKEN: 123}\n",
        'version: 1\ncomponent: backend\napplication: {}\nenvironment: {TOKEN: "secret\\nvalue"}\n',
        "&loop [*loop]",
    ],
)
def test_invalid_yaml_fails_without_leaking_values(tmp_path: Path, invalid: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(invalid)
    with pytest.raises(DeploymentConfigError, match="config.yaml") as caught:
        read_deployment(path)
    assert "secret" not in str(caught.value)


def test_container_adapter_loads_values_before_any_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_root(tmp_path)
    for key in list(os.environ):
        monkeypatch.delenv(key)
    monkeypatch.setenv("CONFIG_FILE", str(path))
    monkeypatch.setenv("WORKER_CONCURRENCY", "7")
    monkeypatch.setattr("deploy.container_entry.sys.argv", ["container_entry.py", "serve"])
    launched: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr("deploy.container_entry.os.execve", lambda *args: launched.append(args))
    container_entry.main()
    assert len(launched) == 1
    executable, arguments, environment = launched[0]
    assert executable == "/usr/local/bin/everunfold-backend"
    assert arguments == [executable, "serve"]
    assert environment["CONFIG_FILE"] == str(path)
    assert environment["WORKER_CONCURRENCY"] == "7"
    assert environment["APPLICATION_SECRET"] == "literal-$$-'quotes'"
    assert environment["EVERUNFOLD_CONFIG_LOADED"] == "1"


def test_missing_root_fails_before_starting_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("CONFIG_FILE", str(path))
    with pytest.raises(ValueError, match="setup.sh --docker") as caught:
        container_entry.main()
    assert str(path) in str(caught.value)


def test_missing_default_never_falls_back_to_toml(tmp_path: Path) -> None:
    from app.core.config import configuration_path

    (tmp_path / "config.toml").write_text("legacy = true")
    assert configuration_path(project_root=tmp_path, environ={}) == tmp_path / "config.yaml"
    with pytest.raises(ConfigurationError, match="config.yaml"):
        load_settings(project_root=tmp_path, environ={})
    for selected in ("", " "):
        with pytest.raises(ConfigurationError, match="cannot be empty"):
            configuration_path(project_root=tmp_path, environ={"CONFIG_FILE": selected})
    custom = tmp_path / "private" / "selected.yml"
    custom.parent.mkdir()
    original = write_root(tmp_path)
    custom.write_bytes(original.read_bytes())
    settings = load_settings(project_root=tmp_path, environ={"CONFIG_FILE": "private/selected.yml"})
    assert settings.project.key == "test-root"
