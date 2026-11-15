"""Explicit root YAML or legacy TOML configuration shared by backend processes."""

import ipaddress
import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from argon2 import Type, extract_parameters
from argon2.exceptions import InvalidHashError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.deployment_config import DeploymentConfigError, read_deployment

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


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_private_network_host(host: str) -> bool:
    """Recognize literal LAN addresses without trusting DNS resolution or forwarded headers."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(
        address in ipaddress.ip_network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    )


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


class QueryCacheConfig(ConfigModel):
    enabled: bool = True
    url: SecretStr | None = None
    ttl_seconds: int = Field(default=45, ge=5, le=300)
    timeout_ms: int = Field(default=35, ge=5, le=500)
    max_value_bytes: int = Field(default=256 * 1024, ge=1024, le=1024 * 1024)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: SecretStr | None) -> SecretStr | None:
        return RedisConfig.valid_url(value) if value is not None else None


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
    settings_receiver_url: str | None = None
    base_url: str
    enabled: bool = False
    nextjs_to_fastapi_token_env: str
    fastapi_to_nextjs_token_env: str

    @field_validator("settings_receiver_url")
    @classmethod
    def valid_receiver(cls, value: str | None) -> str | None:
        if value is not None:
            validate_http_base(value)
            parsed = urlsplit(value)
            if parsed.scheme != "https" and not is_loopback_host(parsed.hostname or ""):
                raise ValueError("internal credentials require HTTPS outside loopback")
        return value

    _base_url = field_validator("base_url")(validate_http_base)

    @field_validator("nextjs_to_fastapi_token_env", "fastapi_to_nextjs_token_env")
    @classmethod
    def valid_reference(cls, value: str) -> str:
        if not _ENV_REFERENCE.fullmatch(value):
            raise ValueError("requires an environment variable reference")
        return value


class NodesMonitorConfig(ConfigModel):
    enabled: bool = False
    origin: str | None = None
    password_hash: SecretStr | None = None
    session_secret_env: str = "EVERUNFOLD_NODES_SESSION_SECRET"
    session_seconds: int = Field(default=1800, ge=300, le=7200)
    trusted_tls_proxy_ips: list[str] = Field(default_factory=list)

    @field_validator("trusted_tls_proxy_ips")
    @classmethod
    def valid_tls_proxy_ips(cls, values: list[str]) -> list[str]:
        return [str(ipaddress.ip_address(value)) for value in values]

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value: str | None) -> str | None:
        return validate_http_base(value) if value is not None else None

    @field_validator("session_secret_env")
    @classmethod
    def valid_secret_reference(cls, value: str) -> str:
        if not _ENV_REFERENCE.fullmatch(value):
            raise ValueError("requires an environment variable reference")
        return value

    @field_validator("password_hash")
    @classmethod
    def valid_password_hash(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                valid = extract_parameters(value.get_secret_value()).type == Type.ID
            except (InvalidHashError, ValueError):
                valid = False
            if not valid:
                raise ValueError("requires a valid password hash")
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
    use_frontend_registry: bool = False

    @field_validator("allowed_origins")
    @classmethod
    def valid_origins(cls, values: list[str]) -> list[str]:
        return [validate_http_base(value) for value in values]


class ReaderPreviewConfig(ConfigModel):
    """Opt-in synthetic reader data; never an authentication or counting service."""

    enabled: bool = False


class AuthConfig(ConfigModel):
    cookie_name: str = "everunfold_session"
    cookie_domain: str | None = None
    cookie_secure: bool | None = None
    allow_private_http: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_seconds: int = Field(default=43200, ge=300, le=604800)
    pre_session_seconds: int = Field(default=900, ge=60, le=3600)
    login_window_seconds: int = Field(default=900, ge=60, le=86400)
    login_attempts_per_account: int = Field(default=10, ge=1, le=100)
    login_attempts_per_ip: int = Field(default=50, ge=1, le=1000)
    bootstrap_attempts_per_ip: int = Field(default=60, ge=1, le=1000)
    application_master_key_env: str = "EVERUNFOLD_API_APPLICATION_MASTER_KEY"
    totp_master_key_env: str = "EVERUNFOLD_TOTP_MASTER_KEY"
    callback_allowed_origins: list[str] = Field(default_factory=list)
    key_cache_seconds: int = Field(default=30, ge=0, le=300)

    @field_validator("application_master_key_env", "totp_master_key_env")
    @classmethod
    def master_reference(cls, value: str) -> str:
        if not _ENV_REFERENCE.fullmatch(value):
            raise ValueError("requires an environment variable reference")
        return value

    @field_validator("callback_allowed_origins")
    @classmethod
    def callback_origins(cls, values: list[str]) -> list[str]:
        return [validate_http_base(value) for value in values]

    @field_validator("cookie_name")
    @classmethod
    def valid_cookie_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) or value.startswith(
            ("__Host-", "__Secure-")
        ):
            raise ValueError("invalid cookie name")
        return value


class MediaConfig(ConfigModel):
    base_url: str | None = Field(default=None, max_length=1024)
    uploads_path: str = "./uploads"

    @field_validator("base_url")
    @classmethod
    def valid_base(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in value
            or any(char.isspace() or ord(char) < 32 for char in value)
            or ".." in parsed.path.split("/")
        ):
            raise ValueError("invalid media base URL")
        validate_http_base(f"{parsed.scheme}://{parsed.netloc}")
        return value.rstrip("/")

    @field_validator("uploads_path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or re.match(r"^[A-Za-z]:[^/\\]", value):
            raise ValueError("requires an absolute or config-relative path")
        return value


class Settings(ConfigModel):
    BACKEND_BASE_URL: str | None = None
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    database: DatabaseConfig
    redis: RedisConfig
    query_cache: QueryCacheConfig = Field(default_factory=QueryCacheConfig)
    celery: CeleryConfig = Field(default_factory=CeleryConfig)
    internal_auth: InternalAuthConfig = Field(default_factory=InternalAuthConfig)
    frontends: dict[str, FrontendConfig] = Field(default_factory=dict)
    nodes_monitor: NodesMonitorConfig = Field(default_factory=NodesMonitorConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    http: HTTPConfig = Field(default_factory=HTTPConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    outbox: OutboxConfig = Field(default_factory=OutboxConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    reader_preview: ReaderPreviewConfig = Field(default_factory=ReaderPreviewConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    _config_directory: Path = PrivateAttr(default=PROJECT_ROOT)

    @property
    def uploads_directory(self) -> Path:
        path = Path(self.media.uploads_path)
        return (path if path.is_absolute() else self._config_directory / path).resolve()

    @property
    def media_base_url(self) -> str:
        return self.media.base_url or (self.BACKEND_BASE_URL or "") + "/media"

    @field_validator("BACKEND_BASE_URL")
    @classmethod
    def valid_backend_url(cls, value: str | None) -> str | None:
        return validate_http_base(value) if value is not None else None

    @property
    def application_query_url(self) -> str:
        return (
            self.BACKEND_BASE_URL + "/v1/auth/api-keys/introspect" if self.BACKEND_BASE_URL else ""
        )

    @model_validator(mode="after")
    def separate_query_cache_instance(self) -> Self:
        def endpoint(url: SecretStr) -> tuple[str | None, int]:
            parsed = urlsplit(url.get_secret_value())
            return parsed.hostname, parsed.port or 6379

        if (
            self.query_cache.enabled
            and self.query_cache.url is not None
            and endpoint(self.query_cache.url)
            in {
                endpoint(self.redis.url),
                endpoint(self.celery.broker_url),
                endpoint(self.celery.result_backend_url),
            }
        ):
            raise ValueError(
                "query_cache requires a Redis instance separate from runtime and broker"
            )
        return self

    @model_validator(mode="after")
    def valid_auth_deployment(self) -> Self:
        if self.BACKEND_BASE_URL is None:
            if self.auth.cookie_secure is False:
                raise ValueError("cookie_secure=false requires an explicit local BACKEND_BASE_URL")
            self.auth.cookie_secure = True
            return self
        url = urlsplit(self.BACKEND_BASE_URL)
        loopback = is_loopback_host(url.hostname or "")
        local_http = loopback or (
            self.auth.allow_private_http and is_private_network_host(url.hostname or "")
        )
        if url.scheme != "https" and not local_http:
            raise ValueError(
                "browser authentication requires HTTPS or explicit private-network HTTP development"
            )
        secure = self.auth.cookie_secure
        if secure is None:
            secure = url.scheme == "https"
            self.auth.cookie_secure = secure
        if not secure and not local_http:
            raise ValueError("insecure cookies require a permitted local development origin")
        if url.scheme == "https" and not secure:
            raise ValueError("HTTPS requires Secure cookies")
        if self.auth.cookie_samesite == "none" and not secure:
            raise ValueError("SameSite=None requires Secure cookies")
        domain = self.auth.cookie_domain
        if domain is not None:
            domain = domain.lstrip(".").lower()
            if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", domain):
                raise ValueError("invalid cookie domain")
            host = url.hostname or ""
            if host != domain and not host.endswith("." + domain):
                raise ValueError("cookie domain must contain the configured API host")
            self.auth.cookie_domain = domain
        return self

    @field_validator("frontends")
    @classmethod
    def valid_nodes(cls, value: dict[str, FrontendConfig]) -> dict[str, FrontendConfig]:
        if any(not _IDENTIFIER.fullmatch(node_id) for node_id in value):
            raise ValueError("invalid frontend node identifier")
        return value

    @model_validator(mode="after")
    def valid_monitor(self) -> Self:
        if self.nodes_monitor.enabled:
            origin = self.nodes_monitor.origin or self.BACKEND_BASE_URL
            if origin is None:
                raise ValueError("nodes_monitor.origin is required without BACKEND_BASE_URL")
            url = urlsplit(origin)
            if url.scheme != "https" and not is_loopback_host(url.hostname or ""):
                raise ValueError("nodes_monitor requires HTTPS or loopback HTTP")
            if self.nodes_monitor.password_hash is None:
                raise ValueError("nodes_monitor requires a password hash")
        return self

    @property
    def redis_prefix(self) -> str:
        scope = f"{self.redis.namespace_scope}:" if self.redis.namespace_scope else ""
        return f"{self.project.key}:{scope}"


_OVERRIDES: dict[str, tuple[str, ...]] = {
    "BACKEND_BASE_URL": ("BACKEND_BASE_URL",),
    "DATABASE_URL": ("database", "url"),
    "REDIS_URL": ("redis", "url"),
    "QUERY_CACHE_REDIS_URL": ("query_cache", "url"),
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
        config_file
        if config_file is not None
        else environment.get(
            "CONFIG_FILE",
            "config.yaml" if (project_root / "config.yaml").is_file() else "config.toml",
        )
    )
    if not str(selected).strip():
        raise ConfigurationError("CONFIG_FILE cannot be empty")
    path = Path(selected)
    path = path if path.is_absolute() else project_root / path
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            document = read_deployment(path)
        except DeploymentConfigError as exc:
            raise ConfigurationError(str(exc)) from None
        data = document["application"]
        environment = {**document.get("environment", {}), **environment}
        data_dir = Path(environment.get("EVERUNFOLD_DATA_DIR", str(project_root / "var/instance")))
        media = data.setdefault("media", {})
        if not isinstance(media, dict):
            raise ConfigurationError("Invalid configuration fields: media")
        uploads = media.setdefault("uploads_path", str(data_dir / "uploads"))
        if isinstance(uploads, str) and not Path(uploads).is_absolute():
            media["uploads_path"] = str(data_dir / uploads)
    else:
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except FileNotFoundError:
            raise ConfigurationError(f"Configuration file not found: {path}") from None
        except (OSError, tomllib.TOMLDecodeError):
            raise ConfigurationError("Configuration file cannot be read or parsed") from None
    if "frontend_nodes_file" in data:
        filename = data.pop("frontend_nodes_file")
        if not isinstance(filename, str) or not filename.strip() or "\x00" in filename:
            raise ConfigurationError("frontend_nodes_file must be a nonempty file path")
        if "frontends" in data:
            raise ConfigurationError("Configure either frontend_nodes_file or inline frontends")
        nodes_path = path.resolve().parent / filename
        try:
            with nodes_path.open("rb") as handle:
                nodes = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError("frontend_nodes_file cannot be read or parsed") from exc
        if set(nodes) != {"frontends"}:
            raise ConfigurationError("frontend_nodes_file must contain only a frontends table")
        data["frontends"] = nodes["frontends"]
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
    if local_cache_url := environment.get("EVERUNFOLD_QUERY_CACHE_DEFAULT_URL"):
        query_cache = data.setdefault("query_cache", {})
        if isinstance(query_cache, dict):
            query_cache.setdefault("url", local_cache_url)
    try:
        settings = Settings.model_validate(data)
        settings._config_directory = path.resolve().parent
        return settings
    except ValidationError as exc:
        # Pydantic's full error rendering includes input values, including credentials.
        locations = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        raise ConfigurationError("Invalid configuration fields: " + ", ".join(locations)) from None
