#!/usr/bin/env python3
"""Review and edit Docker instance configuration (Python 3.11+, POSIX terminal)."""

import ipaddress
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from config_files import Files, env_values, toml_text, update_env
from config_ui import Field, Menu

DOCKER = Path(__file__).resolve().parent
TOKEN_NAMES = ("NEXTJS_TO_FASTAPI_WEB_MAIN_TOKEN", "FASTAPI_TO_NEXTJS_WEB_MAIN_TOKEN")


def ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def valid_port(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{1,5}", value)) and 1 <= int(value) <= 65535


def public_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        local = host == "localhost"
        if ip_address(host):
            address = ipaddress.ip_address(host)
            if address.is_unspecified:
                return False
            local = address.is_loopback
        return (
            (url.scheme == "https" or (url.scheme == "http" and local))
            and bool(host)
            and (url.port is None or url.port > 0)
            and url.username is None
            and url.password is None
            and not re.search(r"\s|[\\?#*]", value)
            and url.path in ("", "/")
        )
    except ValueError:
        return False


def peer_list(value: str) -> bool:
    try:
        for item in value.split(",") if value else []:
            ipaddress.ip_network(item.strip(), strict=False)
        return True
    except ValueError:
        return False


def token(value: str) -> bool:
    return re.fullmatch(r"[!-~]{32,}", value) is not None


def redis_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme in {"redis", "rediss"}
            and bool(url.hostname)
            and (url.port is None or url.port > 0)
            and (url.path in ("", "/") or url.path[1:].isdigit())
        )
    except ValueError:
        return False


def db_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme == "postgresql+asyncpg"
            and bool(url.hostname and url.path.strip("/"))
            and (url.port is None or url.port > 0)
        )
    except ValueError:
        return False


def configure() -> None:
    files = Files()
    compose_path = DOCKER / ".env"
    compose_source = files.read(compose_path)
    compose = env_values(compose_source)
    data = (DOCKER / compose.get("BACKEND_DATA_DIR", "data")).resolve()
    config_path = data / "config.toml"
    env_path = (DOCKER / compose.get("BACKEND_ENV_FILE", "runtime.env")).resolve()
    config = files.toml(config_path)
    env_source = files.read(env_path)
    env = env_values(env_source)
    fields: list[Field] = []
    by_key: dict[str, Field] = {}
    generated: set[str] = set()

    def add(key: str, label: str, value: str = "", **kwargs) -> None:
        field = Field(key, label, str(value), **kwargs)
        fields.append(field)
        by_key[key] = field

    def value(key: str) -> str:
        return by_key[key].value

    def new() -> bool:
        return value("mode") == "1"

    def secret(key: str, label: str, valid) -> None:
        def resolve(text: str) -> str:
            if text:
                generated.discard(key)
                return text
            if new():
                generated.add(key)
                return secrets.token_urlsafe(32)
            return ""

        add(
            key,
            label,
            env.get(key, ""),
            valid=valid,
            secret=True,
            resolve=resolve,
            empty="新站点留空生成；迁移需填写原值",
            hint="输入回显。回车保留已有值；新站点清空后回车生成，迁移时填写原值。",
        )

    auth = config.get("auth", {})
    master = auth.get("application_master_key_env", "EVERUNFOLD_API_APPLICATION_MASTER_KEY")
    totp = auth.get("totp_master_key_env", "EVERUNFOLD_TOTP_MASTER_KEY")
    add(
        "mode",
        "部署类型（1 新站点 / 2 迁移或已有站点）",
        "2" if config or env else "1",
        valid=lambda v: v in ("1", "2"),
    )
    add(
        "api",
        "后端发布 URL",
        env.get("BACKEND_BASE_URL", config.get("BACKEND_BASE_URL", "")),
        valid=public_url,
        resolve=lambda v: v.rstrip("/"),
        hint="资源链接使用的公开根 URL，例如 https://api.example.com；与监听 IP/端口独立。",
    )
    add("host", "API 绑定 IP（容器内）", env.get("API_HOST", "0.0.0.0"), valid=ip_address)
    add("port", "API 监听端口（容器内）", compose.get("BACKEND_API_PORT", "8001"), valid=valid_port)
    add(
        "publish_host",
        "宿主机映射监听 IP",
        compose.get("BACKEND_BIND_ADDRESS", "127.0.0.1").strip("[]"),
        valid=ip_address,
    )
    add(
        "publish_port",
        "宿主机映射端口",
        compose.get("BACKEND_PUBLISH_PORT", "8001"),
        valid=valid_port,
    )
    add(
        "peers",
        "前端内部调用后端的来源 IP/CIDR",
        ", ".join(config.get("internal_auth", {}).get("allowed_peer_ips", ["127.0.0.1", "::1"])),
        valid=peer_list,
        empty="不开放内部调用",
        hint="默认允许本机回环 127.0.0.1、::1，仍需节点令牌。多个 IP/CIDR 用逗号分隔；清空可关闭。"
        "跨容器调用需加入后端实际看到的前端服务或内部代理 IP。",
    )

    def identifier(v: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", v))

    add(
        "project", "项目标识", config.get("project", {}).get("key", "lovepikachu"), valid=identifier
    )
    add(
        "namespace",
        "Redis 命名空间",
        config.get("redis", {}).get("namespace_scope", "dev"),
        valid=lambda v: not v or identifier(v),
        empty="不加环境层",
    )
    add(
        "database",
        "PostgreSQL URL",
        env.get("DATABASE_URL", config.get("database", {}).get("url", "")),
        valid=db_url,
        hint="postgresql+asyncpg://用户:密码@主机:端口/数据库；容器内可达，特殊字符需百分号编码。",
    )
    add(
        "redis",
        "运行 Redis URL",
        env.get("REDIS_URL", config.get("redis", {}).get("url", "redis://redis:6379/0")),
        valid=redis_url,
    )
    add(
        "broker",
        "Celery Broker URL",
        env.get("CELERY_BROKER_URL", config.get("celery", {}).get("broker_url", "")),
        valid=lambda v: not v or redis_url(v),
        empty="沿用运行 Redis 连接，使用 DB 1",
        hint="清空后沿用运行 Redis 的连接参数，使用 DB 1。",
    )
    secret(master, "应用主密钥", lambda v: bool(re.fullmatch(r"[A-Za-z0-9_-]{43,128}", v)))
    secret(totp, "TOTP 主密钥", lambda v: 43 <= len(v) <= 256)
    for label, name in zip(("前端 → 后端令牌", "后端 → 前端令牌"), TOKEN_NAMES, strict=True):
        secret(name, label, token)
    secret("EVERUNFOLD_ADMIN_PASSWORD", "初始管理员密码", lambda v: 12 <= len(v) <= 1024)
    by_key["EVERUNFOLD_ADMIN_PASSWORD"].active = new

    def positive(v: str) -> bool:
        return bool(re.fullmatch(r"[1-9][0-9]*", v))

    add("workers", "API worker 数量", env.get("API_WORKERS", "1"), valid=positive)
    add(
        "pool",
        "Celery worker 池",
        env.get("WORKER_POOL", "solo"),
        valid=lambda v: v in ("solo", "prefork"),
    )
    add(
        "concurrency",
        "Celery worker 并发数",
        env.get("WORKER_CONCURRENCY", "1"),
        valid=positive,
        active=lambda: value("pool") == "prefork",
        hint="solo 固定为 1。",
    )

    def changed(key: str) -> None:
        if key == "mode" and not new():
            for name in generated:
                by_key[name].value = ""
            generated.clear()
        if key == "pool" and value("pool") == "solo":
            by_key["concurrency"].value = "1"

    def check() -> list[str]:
        return (
            ["两个方向的节点令牌必须不同。"]
            if value(TOKEN_NAMES[0]) == value(TOKEN_NAMES[1])
            else []
        )

    def save() -> None:
        config["BACKEND_BASE_URL"] = value("api")
        config.setdefault("project", {})["key"] = value("project")
        config.setdefault("database", {})
        config.setdefault("redis", {})["namespace_scope"] = value("namespace")
        config.setdefault("cors", {}).setdefault("use_frontend_registry", True)
        config.setdefault("internal_auth", {})["allowed_peer_ips"] = [
            item.strip() for item in value("peers").split(",") if item.strip()
        ]
        config.setdefault("media", {}).setdefault("uploads_path", "/data/uploads")
        broker = value("broker") or urlsplit(value("redis"))._replace(path="/1").geturl()
        for section, item, text in (
            ("database", "url", value("database")),
            ("redis", "url", value("redis")),
            ("celery", "broker_url", broker),
        ):
            if item in config.get(section, {}):
                config[section][item] = text
        environment = {
            "API_HOST": value("host"),
            "DATABASE_URL": value("database"),
            "REDIS_URL": value("redis"),
            "CELERY_BROKER_URL": broker,
            "API_WORKERS": value("workers"),
            "WORKER_POOL": value("pool"),
            "WORKER_CONCURRENCY": value("concurrency") if value("pool") == "prefork" else "1",
            **{name: value(name) for name in (master, totp, *TOKEN_NAMES)},
        }
        if "BACKEND_BASE_URL" in env:
            environment["BACKEND_BASE_URL"] = value("api")
        if new():
            environment["EVERUNFOLD_ADMIN_PASSWORD"] = value("EVERUNFOLD_ADMIN_PASSWORD")
        host = value("publish_host")
        mapping = {
            "BACKEND_API_PORT": value("port"),
            "BACKEND_BIND_ADDRESS": f"[{host}]" if ":" in host else host,
            "BACKEND_PUBLISH_PORT": value("publish_port"),
        }
        (data / "uploads").mkdir(parents=True, exist_ok=True)
        files.write(
            {
                config_path: toml_text(config),
                env_path: update_env(env_source, environment),
                compose_path: update_env(compose_source, mapping),
            }
        )
        print("  配置已保存。按 docker/README.md 启动或重新创建容器。")

    Menu("Everunfold Docker · 后端", fields, save=save, check=check, changed=changed).run()


def run() -> None:
    try:
        configure()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消，未保存当前修改。")
        raise SystemExit(1) from None
    except (OSError, ValueError) as error:
        detail = error.strerror if isinstance(error, OSError) else str(error)
        raise SystemExit(f"无法配置：{detail}") from None


if __name__ == "__main__":
    run()
