"""Explicit TOML configuration shared by independent backend processes."""

import ipaddress
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ENV_REFERENCE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class ConfigurationError(ValueError):
    """A sanitized configuration error, safe to show without input values."""


def validate_http_base(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in {"", "/"}
    ):
        raise ValueError("must be an absolute HTTP(S) origin without credentials or API paths")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL port") from exc
    return value.rstrip("/")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


class ProjectConfig(ConfigModel):
    key: str = "lovepikachu"

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid project identifier")
        return value


class DatabaseConfig(ConfigModel):
    url: SecretStr

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme != "postgresql+asyncpg"
            or not parsed.hostname
            or not parsed.path.strip("/")
        ):
            raise ValueError("requires a postgresql+asyncpg URL with host and database")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("invalid database port") from exc
        return value


class RedisConfig(ConfigModel):
    url: SecretStr
    namespace_scope: str = "dev"

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: SecretStr) -> SecretStr:
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("requires an absolute Redis URL")
        if parsed.path not in {"", "/"} and not parsed.path.removeprefix("/").isdigit():
            raise ValueError("invalid Redis database number")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("invalid Redis port") from exc
        return value

    @field_validator("namespace_scope")
    @classmethod
    def valid_scope(cls, value: str) -> str:
        if value and not _IDENTIFIER.fullmatch(value):
            raise ValueError("invalid Redis namespace scope")
        return value


class CeleryConfig(ConfigModel):
    broker_url: SecretStr = SecretStr("redis://127.0.0.1:6379/1")
    result_backend_url: SecretStr = SecretStr("redis://127.0.0.1:6379/2")

    @field_validator("broker_url", "result_backend_url")
    @classmethod
    def valid_url(cls, value: SecretStr) -> SecretStr:
        return RedisConfig.valid_url(value)


class InternalAuthConfig(ConfigModel):
    allowed_peer_ips: list[str] = Field(default_factory=list)

    @field_validator("allowed_peer_ips")
    @classmethod
    def valid_networks(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError("invalid IP address or CIDR") from exc
        return values


class FrontendConfig(ConfigModel):
    base_url: str
    enabled: bool = False
    nextjs_to_fastapi_token_env: str
    fastapi_to_nextjs_token_env: str

    _base_url = field_validator("base_url")(validate_http_base)

    @field_validator("nextjs_to_fastapi_token_env", "fastapi_to_nextjs_token_env")
    @classmethod
    def valid_reference(cls, value: str) -> str:
        if not _ENV_REFERENCE.fullmatch(value):
            raise ValueError("requires an environment variable reference")
        return value


class RuntimeConfig(ConfigModel):
    health_check_timeout_seconds: int = Field(default=2, ge=1, le=30)
    http_shutdown_grace_seconds: int = Field(default=30, ge=1)
    worker_shutdown_warn_after_seconds: int = Field(default=120, ge=1)


class HTTPConfig(ConfigModel):
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=100)
    idempotency_retention_seconds: int = Field(default=86400, ge=1)


class LimitsConfig(ConfigModel):
    json_body_max_bytes: int = Field(default=8388608, ge=1)
    upload_max_bytes: int = Field(default=33554432, ge=1)
    ws_update_max_bytes: int = Field(default=1048576, ge=1)
    ws_pending_max_bytes_per_connection: int = Field(default=4194304, ge=1)
    ws_auth_timeout_seconds: int = Field(default=5, ge=1)


class OutboxConfig(ConfigModel):
    poll_interval_seconds: int = Field(default=1, ge=1)
    batch_size: int = Field(default=50, ge=1)
    connect_timeout_seconds: int = Field(default=2, ge=1)
    request_timeout_seconds: int = Field(default=15, ge=1)
    max_attempts: int = Field(default=8, ge=1)
    retry_max_delay_seconds: int = Field(default=900, ge=1)
    lease_seconds: int = Field(default=60, ge=1)


class CORSConfig(ConfigModel):
    allowed_origins: list[str] = Field(default_factory=list)

    @field_validator("allowed_origins")
    @classmethod
    def valid_origins(cls, values: list[str]) -> list[str]:
        return [validate_http_base(value) for value in values]


class ReaderPreviewConfig(ConfigModel):
    """Opt-in synthetic reader data; never an authentication or counting service."""

    enabled: bool = False


class Settings(ConfigModel):
    BACKEND_BASE_URL: str
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    database: DatabaseConfig
    redis: RedisConfig
    celery: CeleryConfig = Field(default_factory=CeleryConfig)
    internal_auth: InternalAuthConfig = Field(default_factory=InternalAuthConfig)
    frontends: dict[str, FrontendConfig] = Field(default_factory=dict)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    http: HTTPConfig = Field(default_factory=HTTPConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    outbox: OutboxConfig = Field(default_factory=OutboxConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    reader_preview: ReaderPreviewConfig = Field(default_factory=ReaderPreviewConfig)

    _backend_url = field_validator("BACKEND_BASE_URL")(validate_http_base)

    @field_validator("frontends")
    @classmethod
    def valid_nodes(cls, value: dict[str, FrontendConfig]) -> dict[str, FrontendConfig]:
        if any(not _IDENTIFIER.fullmatch(node_id) for node_id in value):
            raise ValueError("invalid frontend node identifier")
        return value

    @property
    def redis_prefix(self) -> str:
        scope = f"{self.redis.namespace_scope}:" if self.redis.namespace_scope else ""
        return f"{self.project.key}:{scope}"


_OVERRIDES: dict[str, tuple[str, ...]] = {
    "BACKEND_BASE_URL": ("BACKEND_BASE_URL",),
    "DATABASE_URL": ("database", "url"),
    "REDIS_URL": ("redis", "url"),
    "CELERY_BROKER_URL": ("celery", "broker_url"),
    "CELERY_RESULT_BACKEND_URL": ("celery", "result_backend_url"),
}


def load_settings(
    config_file: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Settings:
    """Load an explicit file then allowlisted overrides; never auto-load .env files."""
    environment = os.environ if environ is None else environ
    selected = (
        config_file if config_file is not None else environment.get("CONFIG_FILE", "config.toml")
    )
    if not str(selected).strip():
        raise ConfigurationError("CONFIG_FILE cannot be empty")
    path = Path(selected)
    path = path if path.is_absolute() else project_root / path
    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {path.name}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError("Configuration file cannot be read or parsed") from exc
    for variable, keys in _OVERRIDES.items():
        if variable not in environment:
            continue
        value = environment[variable]
        if not value.strip():
            raise ConfigurationError(f"{variable} cannot be empty")
        target = data
        for key in keys[:-1]:
            nested = target.setdefault(key, {})
            if not isinstance(nested, dict):
                raise ConfigurationError(f"Invalid configuration section: {key}")
            target = nested
        target[keys[-1]] = value
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        # Pydantic's full error rendering includes input values, including credentials.
        locations = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        raise ConfigurationError("Invalid configuration fields: " + ", ".join(locations)) from None
