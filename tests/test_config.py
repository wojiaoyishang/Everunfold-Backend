from pathlib import Path

import pytest

from app.core.config import ConfigurationError, load_settings
from app.core.redis_keys import redis_channel, redis_key

VALID_CONFIG = """
BACKEND_BASE_URL = "http://127.0.0.1:8000"
[project]
key = "lovepikachu"
[database]
url = "postgresql+asyncpg://postgres:private-password@127.0.0.1:5432/everunfold_test"
[redis]
url = "redis://127.0.0.1:6379/0"
namespace_scope = "test-config"
"""


def test_config_root_is_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.toml").write_text(VALID_CONFIG, encoding="utf-8")
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    settings = load_settings(project_root=tmp_path, environ={})
    assert settings.project.key == "lovepikachu"
    assert settings.redis_prefix == "lovepikachu:test-config:"
    assert "private-password" not in repr(settings)
    assert "private-password" not in settings.model_dump_json()


def test_explicit_env_override_and_relative_file(tmp_path: Path) -> None:
    (tmp_path / "local.toml").write_text(VALID_CONFIG, encoding="utf-8")
    replacement = "postgresql+asyncpg://test:replacement@127.0.0.1:5432/other_test"
    settings = load_settings(
        project_root=tmp_path,
        environ={"CONFIG_FILE": "local.toml", "DATABASE_URL": replacement},
    )
    assert settings.database.url.get_secret_value() == replacement


@pytest.mark.parametrize("variable", ["CONFIG_FILE", "DATABASE_URL", "REDIS_URL"])
def test_empty_overrides_never_fall_back(tmp_path: Path, variable: str) -> None:
    (tmp_path / "config.toml").write_text(VALID_CONFIG, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="cannot be empty"):
        load_settings(project_root=tmp_path, environ={variable: ""})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.replace('key = "lovepikachu"', 'key = "lovepikachu:*"'),
        lambda value: value.replace("namespace_scope", "namespace_scpoe"),
        lambda value: value.replace('namespace_scope = "test-config"', "namespace_scope = 1"),
        lambda value: value.replace("http://127.0.0.1:8000", "http://user:private-password@host"),
    ],
)
def test_invalid_settings_are_rejected_without_secret_values(
    tmp_path: Path, mutation: object
) -> None:
    assert callable(mutation)
    (tmp_path / "config.toml").write_text(mutation(VALID_CONFIG), encoding="utf-8")
    with pytest.raises(ConfigurationError) as error:
        load_settings(project_root=tmp_path, environ={})
    assert "private-password" not in str(error.value)


def test_shared_key_generation_has_environment_scope(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(VALID_CONFIG, encoding="utf-8")
    settings = load_settings(project_root=tmp_path, environ={})
    assert redis_key(settings, "task", "123", "progress") == (
        "lovepikachu:test-config:task:123:progress"
    )
    assert redis_channel(settings, "broadcast", "api") == "lovepikachu:test-config:broadcast:api"
    with pytest.raises(ValueError):
        redis_key(settings, "*")


def test_media_path_uses_config_directory_and_cdn_prefix(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    file = config_dir / "local.toml"
    file.write_text(
        VALID_CONFIG
        + '\n[media]\nuploads_path = "./uploads"\nbase_url = "https://cdn.example.test/assets/"\n',
        encoding="utf-8",
    )
    settings = load_settings(file, environ={})
    assert settings.uploads_directory == (config_dir / "uploads").resolve()
    assert settings.media_base_url == "https://cdn.example.test/assets"
    absolute = (tmp_path / "absolute").as_posix()
    file.write_text(VALID_CONFIG + f'\n[media]\nuploads_path = "{absolute}"\n', encoding="utf-8")
    assert load_settings(file, environ={}).uploads_directory == Path(absolute).resolve()
    assert load_settings(file, environ={}).media_base_url == "http://127.0.0.1:8000/media"


@pytest.mark.parametrize(
    "base",
    [
        "javascript:alert(1)",
        "https://u:p@cdn.test/a",
        "https://cdn.test/a?secret=1",
        "https://cdn.test/a#b",
        "https://cdn.test/a/../b",
        "https://cdn.test/" + "a" * 1024,
    ],
)
def test_invalid_media_base_is_rejected(tmp_path: Path, base: str) -> None:
    file = tmp_path / "local.toml"
    file.write_text(VALID_CONFIG + f'\n[media]\nbase_url = "{base}"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(file, environ={})


@pytest.mark.parametrize("host", ["10.0.0.2", "172.16.3.2", "192.168.1.2", "[fd00::2]"])
def test_private_http_requires_explicit_opt_in(tmp_path: Path, host: str) -> None:
    file = tmp_path / "config.toml"
    content = VALID_CONFIG.replace("http://127.0.0.1:8000", f"http://{host}:3000")
    file.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_settings(file, environ={})
    file.write_text(content + "\n[auth]\nallow_private_http = true\n", encoding="utf-8")
    assert load_settings(file, environ={}).auth.cookie_secure is False


@pytest.mark.parametrize("host", ["8.8.8.8", "172.32.0.1", "169.254.1.2", "0.0.0.0", "api.test"])
def test_private_http_opt_in_never_allows_other_hosts(tmp_path: Path, host: str) -> None:
    file = tmp_path / "config.toml"
    file.write_text(
        VALID_CONFIG.replace("http://127.0.0.1:8000", f"http://{host}:3000")
        + "\n[auth]\nallow_private_http = true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_settings(file, environ={})


@pytest.mark.parametrize(
    "origin, extra",
    [
        ("https://172.16.3.2:3000", "cookie_secure = false"),
        ("http://172.16.3.2:3000", 'cookie_samesite = "none"'),
    ],
)
def test_private_http_keeps_https_and_samesite_cookie_rules(
    tmp_path: Path, origin: str, extra: str
) -> None:
    file = tmp_path / "config.toml"
    file.write_text(
        VALID_CONFIG.replace("http://127.0.0.1:8000", origin)
        + f"\n[auth]\nallow_private_http = true\n{extra}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_settings(file, environ={})
