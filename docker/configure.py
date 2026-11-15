#!/usr/bin/env python3
"""Interactive Docker configuration for a new instance."""

import getpass
import ipaddress
import json
import os
import re
import secrets
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

DOCKER = Path(__file__).resolve().parent
DATA = DOCKER / "data"
TOKEN_NAMES = ("NEXTJS_TO_FASTAPI_WEB_MAIN_TOKEN", "FASTAPI_TO_NEXTJS_WEB_MAIN_TOKEN")


def section(title: str) -> None:
    print(f"\n── {title} ─────────────────────────────")


def ask(
    label: str,
    default: str = "",
    *,
    hidden: bool = False,
    valid: Callable[[str], bool] = bool,
) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        read = getpass.getpass if hidden else input
        value = read(f"  {label}{suffix}: ")
        if not hidden:
            value = value.strip()
        value = value or default
        if valid(value) and all(ord(char) >= 32 and ord(char) != 127 for char in value):
            return value
        print("  格式不正确，请重试。")


def origin(value: str, *, https: bool = False) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme in ({"https"} if https else {"http", "https"})
            and bool(url.hostname)
            and (url.port is None or url.port > 0)
            and not (url.username or url.password or url.query or url.fragment)
            and not re.search(r"\s|[\\?#]", value)
            and url.path in ("", "/")
        )
    except ValueError:
        return False


def peers(label: str) -> list[str]:
    def valid(value: str) -> bool:
        try:
            for item in value.split(","):
                ipaddress.ip_network(item.strip(), strict=False)
            return True
        except ValueError:
            return False

    return [item.strip() for item in ask(label, valid=valid).split(",")]


def token(value: str) -> bool:
    return re.fullmatch(r"[!-~]{32,}", value) is not None


def env_text(values: dict[str, str]) -> str:
    # Compose interprets $$ as a literal dollar sign in double-quoted values.
    return "".join(
        f"{name}={json.dumps(value.replace('$', '$$'), ensure_ascii=False)}\n"
        for name, value in values.items()
    )


def write_new(files: dict[Path, str]) -> None:
    written = []
    try:
        for file, content in files.items():
            file.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written.append(file)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
    except OSError:
        for file in written:
            file.unlink(missing_ok=True)
        raise
    for file in written:
        print(f"  已写入 {file.relative_to(DOCKER)}")


TARGETS = (DATA / "config.toml", DATA / "frontend-nodes.toml", DOCKER / "runtime.env")


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


def automatic(label: str, valid: Callable[[str], bool], *, new: bool = True) -> str:
    hint = "留空自动生成" if new else "填写已有值"
    value = ask(
        f"{label}（{hint}）", hidden=True, valid=lambda value: (new and not value) or valid(value)
    )
    return value or secrets.token_urlsafe(32)


def backend_origin(value: str) -> bool:
    if not origin(value):
        return False
    url = urlsplit(value)
    return url.scheme == "https" or url.hostname in {"localhost", "127.0.0.1", "::1"}


def configure() -> None:
    section("Everunfold Docker · 后端")
    print("  新站点可生成密钥。迁移已有站点时请带上原密钥和节点令牌。")
    new = ask("部署类型（1 新站点，2 迁移已有站点）", "1", valid=lambda v: v in ("1", "2")) == "1"
    section("公开地址与前端节点")
    api = ask("浏览器访问的 API 地址", "http://127.0.0.1:8001", valid=backend_origin).rstrip("/")
    site = ask("前端公开地址", "http://127.0.0.1:3000", valid=origin).rstrip("/")
    node = ask(
        "前端节点 ID",
        "web-main",
        valid=lambda v: bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", v)),
    )
    receiver = ask(
        "前端接收器 HTTPS 地址（留空暂不启用节点）", valid=lambda v: not v or origin(v, https=True)
    ).rstrip("/")
    allowed = peers("FastAPI 实际看到的前端或内部代理 IP/CIDR，逗号分隔")

    section("PostgreSQL 与 Redis")
    print("  使用容器内可达的地址。URL 中密码的特殊字符需百分号编码。")
    identifier = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    project = ask("项目标识", "lovepikachu", valid=lambda v: bool(identifier.fullmatch(v)))
    namespace = ask("Redis 命名空间", "dev", valid=lambda v: bool(identifier.fullmatch(v)))
    database = ask("PostgreSQL URL（postgresql+asyncpg://…）", hidden=True, valid=db_url)
    redis = ask("运行 Redis URL", "redis://redis:6379/0", hidden=True, valid=redis_url)
    default_broker = urlsplit(redis)._replace(path="/1").geturl()
    print("  Broker 留空沿用运行 Redis 的连接参数，使用 DB 1。")
    broker = (
        ask("Celery Broker URL", hidden=True, valid=lambda v: not v or redis_url(v))
        or default_broker
    )

    section("密钥与管理员")
    env = {"DATABASE_URL": database, "REDIS_URL": redis, "CELERY_BROKER_URL": broker}
    env["EVERUNFOLD_API_APPLICATION_MASTER_KEY"] = automatic(
        "应用主密钥", lambda v: bool(re.fullmatch(r"[A-Za-z0-9_-]{43,128}", v)), new=new
    )
    env["EVERUNFOLD_TOTP_MASTER_KEY"] = automatic(
        "TOTP 主密钥", lambda v: 43 <= len(v) <= 256, new=new
    )
    for label, name in zip(("前端 → 后端令牌", "后端 → 前端令牌"), TOKEN_NAMES, strict=True):
        env[name] = automatic(label, token, new=new)
    if env[TOKEN_NAMES[0]] == env[TOKEN_NAMES[1]]:
        raise SystemExit("两个方向的令牌必须不同，配置未写入。")
    if new:
        env["EVERUNFOLD_ADMIN_PASSWORD"] = automatic(
            "初始管理员密码", lambda v: 12 <= len(v) <= 1024
        )
    env["API_WORKERS"] = ask(
        "API worker 数量", "1", valid=lambda v: bool(re.fullmatch(r"[1-9][0-9]*", v))
    )
    pool = ask("Celery worker 池", "solo", valid=lambda v: v in ("solo", "prefork"))
    env["WORKER_POOL"] = pool
    env["WORKER_CONCURRENCY"] = (
        "1"
        if pool == "solo"
        else ask("Celery worker 并发数", "1", valid=lambda v: bool(re.fullmatch(r"[1-9][0-9]*", v)))
    )

    config = f"""BACKEND_BASE_URL = {json.dumps(api)}
frontend_nodes_file = "frontend-nodes.toml"

[project]
key = {json.dumps(project)}

[database]
# DATABASE_URL 由 runtime.env 注入。

[redis]
# REDIS_URL 由 runtime.env 注入。
namespace_scope = {json.dumps(namespace)}

[cors]
allowed_origins = {json.dumps([site])}

[internal_auth]
allowed_peer_ips = {json.dumps(allowed)}

[media]
uploads_path = "/data/uploads"
"""
    nodes = f"""[frontends.{node}]
base_url = {json.dumps(site)}
enabled = {str(bool(receiver)).lower()}
nextjs_to_fastapi_token_env = "{TOKEN_NAMES[0]}"
fastapi_to_nextjs_token_env = "{TOKEN_NAMES[1]}"
"""
    if receiver:
        nodes += f"settings_receiver_url = {json.dumps(receiver)}\n"

    section("保存")
    print(f"  API {api} · 前端 {site} · 节点 {node} · Redis 命名空间 {project}:{namespace}")
    if ask("写入后端配置（y/N）", "n").lower() != "y":
        print("  已取消，未写入。")
        return
    (DATA / "uploads").mkdir(parents=True, exist_ok=True)
    write_new(dict(zip(TARGETS, (config, nodes, env_text(env)), strict=True)))
    print("  按 docker/README.md 初始化或检查数据库，再启动后端。")
    if new:
        print("  初始管理员密码保存在私有 runtime.env 中。")


def run() -> None:
    if not sys.stdin.isatty():
        raise SystemExit("请从交互式终端运行此向导。")
    if any(file.exists() for file in TARGETS):
        raise SystemExit("检测到现有配置。请直接编辑已有文件，向导不会覆盖配置或轮换密钥。")
    try:
        configure()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消配置。")
        raise SystemExit(1) from None
    except OSError as error:
        raise SystemExit(f"无法保存配置：{error.strerror}") from None


if __name__ == "__main__":
    run()
