#!/usr/bin/env python3
"""Everunfold backend deployment configuration."""

import argparse
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from config_actions import export_node
from config_context import Context
from config_files import toml_text, update_env
from config_model import Configuration, Field
from config_probes import backend_checks
from config_validation import (
    db_url,
    identifier,
    ip_address,
    peer_list,
    positive,
    private_receiver_url,
    public_url,
    redis_url,
    token,
    valid_port,
)
from container_fields import add_container_fields
from node_console_fields import add_node_console_fields

DOCKER = Path(__file__).resolve().parents[1] / "docker"
TOKEN_NAMES = ("NEXTJS_TO_FASTAPI_WEB_MAIN_TOKEN", "FASTAPI_TO_NEXTJS_WEB_MAIN_TOKEN")


def node_token_names(node: str) -> tuple[str, str]:
    if node == "web-main":
        return TOKEN_NAMES
    suffix = hashlib.sha256(node.encode()).hexdigest()[:16].upper()
    return (f"NEXTJS_TO_FASTAPI_NODE_{suffix}_TOKEN", f"FASTAPI_TO_NEXTJS_NODE_{suffix}_TOKEN")


def configure(native: bool = False) -> Configuration:
    ctx = Context(DOCKER, "backend", native)
    config, env, compose = ctx.config, ctx.env, ctx.compose
    fields: list[Field] = []
    by_key: dict[str, Field] = {}
    generated: set[str] = set()
    node_values: dict[str, tuple[str, str]] = {}
    selected_node = str(ctx.meta.get("node", "web-main"))

    def add(key: str, label: str, value: str = "", **kwargs) -> Field:
        item = Field(key, label, str(value), **kwargs)
        fields.append(item)
        by_key[key] = item
        return item

    def value(key: str) -> str:
        return by_key[key].value

    def new() -> bool:
        return value("mode") == "1"

    def secret(
        key: str,
        label: str,
        valid,
        *,
        allow_generate: bool = True,
        initial: str | None = None,
        group: str = "密钥",
    ) -> Field:
        def generate() -> str:
            if not new() and not allow_generate:
                raise ValueError("已有站点需保留原主密钥。")
            generated.add(key)
            return secrets.token_urlsafe(32)

        initial = env.get(key, "") if initial is None else initial
        if not initial and new():
            initial = generate()
        return add(
            key,
            label,
            initial,
            secret=True,
            valid=valid,
            group=group,
            generate=generate if allow_generate or new() else None,
            resolve=lambda v: v or (generate() if new() or allow_generate else ""),
            hint="已自动生成或读取原值，可直接编辑。已有站点主密钥需保留。",
            empty="请填写原值",
        )

    container_mapping, container_errors, pending_network = add_container_fields(ctx, add, value)

    add(
        "mode",
        "部署类型（1 新站点 / 2 已有站点）",
        "2" if config or env else "1",
        valid=lambda v: v in ("1", "2"),
    )
    add(
        "api",
        "后端发布 URL",
        env.get("BACKEND_BASE_URL", config.get("BACKEND_BASE_URL", "http://127.0.0.1:8001")),
        valid=public_url,
        resolve=lambda v: v.rstrip("/"),
        hint="公开资源链接的根地址，例如 https://api.example.com，与绑定地址、端口独立。",
    )
    add("project", "项目标识", config.get("project", {}).get("key", "everunfold"), valid=identifier)
    add(
        "database",
        "PostgreSQL URL",
        env.get("DATABASE_URL", config.get("database", {}).get("url", "")),
        valid=db_url,
        group="数据库",
        hint="可点击“分项填写”，自动编码用户名和密码中的特殊字符。",
    )
    add(
        "auto_init_empty_db",
        "空库自动初始化",
        env.get("EVERUNFOLD_AUTO_INIT_EMPTY_DB", "1"),
        valid=lambda v: v in ("0", "1"),
        group="数据库",
        hint=(
            "1 开启（默认）/ 0 关闭。构建并启动或 start 时，仅对确认无用户对象的空库初始化；"
            "已有库仍需显式迁移。"
        ),
    )
    add(
        "redis",
        "运行 Redis URL",
        env.get(
            "REDIS_URL",
            config.get("redis", {}).get(
                "url", "redis://127.0.0.1:6379/0" if native else "redis://redis:6379/0"
            ),
        ),
        valid=redis_url,
        group="数据库",
    )
    add(
        "broker",
        "Celery Broker URL",
        env.get("CELERY_BROKER_URL", config.get("celery", {}).get("broker_url", "")),
        valid=lambda v: not v or redis_url(v),
        group="数据库",
        empty="沿用 Redis，使用 DB 1",
    )
    add(
        "namespace",
        "Redis 命名空间",
        config.get("redis", {}).get("namespace_scope", "production"),
        valid=lambda v: not v or identifier(v),
        group="数据库",
        empty="不加环境层",
    )
    auth = config.get("auth", {})
    master = auth.get("application_master_key_env", "EVERUNFOLD_API_APPLICATION_MASTER_KEY")
    totp = auth.get("totp_master_key_env", "EVERUNFOLD_TOTP_MASTER_KEY")
    secret(
        master,
        "应用主密钥",
        lambda v: bool(re.fullmatch(r"[A-Za-z0-9_-]{43,128}", v)),
        allow_generate=False,
    )
    secret(totp, "TOTP 主密钥", lambda v: 43 <= len(v) <= 256, allow_generate=False)
    secret(
        "EVERUNFOLD_ADMIN_PASSWORD", "初始管理员密码", lambda v: not new() or 12 <= len(v) <= 1024
    ).hint = "用于首次创建管理员，可查看原值；修改不会重设已创建账号的密码。"

    add(
        "node",
        "待配对的前端节点 ID",
        selected_node,
        valid=identifier,
        group="前端配对",
        hint="每个前端使用独立节点 ID 和令牌。保存后可在 /_nodes 沿用该配对并启用。",
    )
    add(
        "receiver",
        "此节点的内部接收器 URL",
        ctx.meta.get("receiver", ""),
        valid=lambda v: (
            not v
            or (private_receiver_url(v) if value("receiver_private_http") == "1" else public_url(v))
        ),
        resolve=lambda v: v.rstrip("/"),
        group="前端配对",
        empty="登记并导出节点前填写",
        hint="与网页共用端口。推荐 http://everunfold-frontend:3000（同一自定义网络）；宿主网关须使用宿主映射端口。",
    )
    add(
        "receiver_private_http",
        "允许私有网络 HTTP 接收（1/0）",
        "1" if ctx.meta.get("allow_private_http", not native) else "0",
        valid=lambda v: v in {"0", "1"},
        group="前端配对",
        hint=(
            "仅用于可信容器网络：别名、私有 IP 或 host.docker.internal。"
            "公网仍须 HTTPS；来源 IP 和令牌验证保留。"
        ),
    )
    names = node_token_names(selected_node)
    for key, label, name in zip(
        ("inbound", "outbound"), ("前端 → 后端令牌", "后端 → 前端令牌"), names, strict=True
    ):
        secret(
            key, label, token, initial=env.get(name) or secrets.token_urlsafe(32), group="前端配对"
        )
    node_values[selected_node] = (value("inbound"), value("outbound"))
    add(
        "peers",
        "前端内部调用后端的来源 IP/CIDR",
        ", ".join(config.get("internal_auth", {}).get("allowed_peer_ips", ctx.peers())),
        valid=peer_list,
        group="前端配对",
        empty="关闭内部调用",
        hint="逗号分隔，默认本机回环。容器设置可自动追加所选网络 CIDR；仍须正确令牌。",
    )

    save_console = add_node_console_fields(ctx, add, value)

    add(
        "host",
        "API 绑定 IP",
        env.get("API_HOST", "127.0.0.1" if native else "0.0.0.0"),
        valid=ip_address,
        group="进程",
    )
    add(
        "port",
        "API 监听端口",
        env.get("API_PORT", "8001") if native else compose.get("BACKEND_API_PORT", "8001"),
        valid=valid_port,
        group="进程",
    )
    add("workers", "API worker 数量", env.get("API_WORKERS", "1"), valid=positive, group="进程")
    add(
        "pool",
        "Celery worker 池",
        env.get("WORKER_POOL", "solo"),
        valid=lambda v: v in ("solo", "prefork"),
        group="进程",
    )
    add(
        "concurrency",
        "Celery worker 并发数",
        env.get("WORKER_CONCURRENCY", "1"),
        valid=positive,
        active=lambda: value("pool") == "prefork",
        group="进程",
    )
    log_levels = {
        "API_LOG_LEVEL": ("API 日志等级", "INFO"),
        "WORKER_LOG_LEVEL": ("Celery Worker 日志等级", "WARNING"),
        "SCHEDULER_LOG_LEVEL": ("Scheduler 日志等级", "INFO"),
    }
    for key, (label, default) in log_levels.items():
        add(
            key,
            label,
            env.get(key, default).upper(),
            resolve=lambda v: v.upper(),
            valid=lambda v: v in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
            group="日志",
            hint=(
                "DEBUG / INFO / WARNING / ERROR / CRITICAL。WARNING 隐藏常规任务日志；"
                "API 使用 INFO 可查看配置加载，WARNING 保留访问拒绝原因。保存并启动后生效。"
            ),
        )
    add(
        "publish_host",
        "宿主映射绑定 IP",
        compose.get("BACKEND_BIND_ADDRESS", "127.0.0.1").strip("[]"),
        valid=lambda s: "%" not in s and ip_address(s),
        hint="127.0.0.1 供宿主反代；0.0.0.0 绑定全部 IPv4 接口；也可填宿主具体 IPv4 或 IPv6。",
        active=lambda: not native,
        group="Docker",
    )
    add(
        "publish_port",
        "宿主映射端口",
        compose.get("BACKEND_PUBLISH_PORT", "8001"),
        valid=valid_port,
        active=lambda: not native,
        group="Docker",
    )
    add(
        "image",
        "镜像名称",
        compose.get("BACKEND_IMAGE", "everunfold-backend:python312"),
        valid=lambda v: bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/:@-]+", v)),
        active=lambda: not native,
        group="Docker",
    )

    def changed(key: str) -> None:
        nonlocal selected_node
        if key == "mode" and not new():
            for name in generated:
                if name in (master, totp, "EVERUNFOLD_ADMIN_PASSWORD"):
                    by_key[name].value = ""
                    by_key[name].generate = None
            generated.clear()
        if key == "pool" and value("pool") == "solo":
            by_key["concurrency"].value = "1"
        if key == "node":
            node_values[selected_node] = (value("inbound"), value("outbound"))
            selected_node = value("node")
            incoming, outgoing = node_values.setdefault(
                selected_node,
                tuple(
                    env.get(name) or secrets.token_urlsafe(32)
                    for name in node_token_names(selected_node)
                ),
            )
            by_key["inbound"].value, by_key["outbound"].value = incoming, outgoing

    def check() -> list[str]:
        return container_errors() + (
            ["两个方向的节点令牌必须不同。"] if value("inbound") == value("outbound") else []
        )

    def broker() -> str:
        return value("broker") or urlsplit(value("redis"))._replace(path="/1").geturl()

    async def probe():
        if pending_network():
            return pending_network()
        source = (DOCKER / compose.get("BACKEND_SOURCE_DIR", "..")).resolve()
        return await backend_checks(value("database"), value("redis"), broker(), source)

    def save() -> None:
        config["BACKEND_BASE_URL"] = value("api")
        config.setdefault("project", {})["key"] = value("project")
        config.setdefault("database", {})
        config.setdefault("redis", {})["namespace_scope"] = value("namespace")
        config.setdefault("cors", {}).setdefault("use_frontend_registry", True)
        config.setdefault("internal_auth", {})["allowed_peer_ips"] = [
            item.strip() for item in value("peers").split(",") if item.strip()
        ]
        config.setdefault("media", {}).setdefault("uploads_path", ctx.runtime_path("uploads"))
        for section, item, text in (
            ("database", "url", value("database")),
            ("redis", "url", value("redis")),
            ("celery", "broker_url", broker()),
        ):
            if item in config.get(section, {}):
                config[section][item] = text
        node_names = node_token_names(value("node"))
        environment = {
            "API_HOST": value("host"),
            "API_PORT": value("port"),
            "DATABASE_URL": value("database"),
            "EVERUNFOLD_AUTO_INIT_EMPTY_DB": value("auto_init_empty_db"),
            "REDIS_URL": value("redis"),
            "CELERY_BROKER_URL": broker(),
            "API_WORKERS": value("workers"),
            "WORKER_POOL": value("pool"),
            "WORKER_CONCURRENCY": value("concurrency") if value("pool") == "prefork" else "1",
            master: value(master),
            totp: value(totp),
            node_names[0]: value("inbound"),
            node_names[1]: value("outbound"),
        }
        environment.update({key: value(key) for key in log_levels})
        if "BACKEND_BASE_URL" in env:
            environment["BACKEND_BASE_URL"] = value("api")
        if value("EVERUNFOLD_ADMIN_PASSWORD"):
            environment["EVERUNFOLD_ADMIN_PASSWORD"] = value("EVERUNFOLD_ADMIN_PASSWORD")
        save_console(environment)
        host = value("publish_host")
        mapping = (
            {}
            if native
            else {
                "BACKEND_API_PORT": value("port"),
                "BACKEND_BIND_ADDRESS": f"[{host}]" if ":" in host else host,
                "BACKEND_PUBLISH_PORT": value("publish_port"),
                "BACKEND_IMAGE": value("image"),
            }
        )
        if not native:
            mapping.setdefault("EVERUNFOLD_UID", compose.get("EVERUNFOLD_UID", str(os.getuid())))
            mapping.setdefault("EVERUNFOLD_GID", compose.get("EVERUNFOLD_GID", str(os.getgid())))
        mapping.update(container_mapping())
        ctx.write(
            {
                ctx.config_path: toml_text(config),
                ctx.env_path: update_env(ctx.env_source, environment),
                ctx.compose_path: update_env(ctx.compose_source, mapping),
                ctx.meta_path: json.dumps(
                    {
                        "node": value("node"),
                        "receiver": value("receiver"),
                        "allow_private_http": value("receiver_private_http") == "1",
                        "inbound_env": node_names[0],
                        "outbound_env": node_names[1],
                    },
                    indent=2,
                )
                + "\n",
            }
        )

    return Configuration(
        "Everunfold Backend",
        fields,
        save=save,
        check=check,
        changed=changed,
        probe=probe,
        export_node=lambda: export_node(DOCKER.parent, native),
        native=native,
        backend=True,
        location=str(ctx.root_path),
    )


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    try:
        from config_ui import run as ui_run

        raise SystemExit(ui_run(configure(args.native)))
    except (KeyboardInterrupt, EOFError):
        raise SystemExit(10) from None
    except (OSError, ValueError) as error:
        detail = error.strerror if isinstance(error, OSError) else str(error)
        raise SystemExit(f"无法配置：{detail}") from None


if __name__ == "__main__":
    run()
