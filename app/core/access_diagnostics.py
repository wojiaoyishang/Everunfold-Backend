"""Server-only access diagnostics; never include credentials or request bodies."""

import ipaddress
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import Literal

from fastapi import Request

from app.core.request_ip import RequestIPCache, SavedRequestIPPolicy, ip_report

# Inherit the ASGI server's stderr handler and INFO level, including with access logs off.
logger = logging.getLogger("uvicorn.error.everunfold.access")
SyncBoundary = Literal[
    "internal_extensions",
    "internal_node_sync",
    "internal_node_recovery",
    "internal_request_ip_policy",
]
AccessBoundary = Literal["nodes_monitor", SyncBoundary]
_recent: OrderedDict[str, float] = OrderedDict()
_reasons = {
    "monitor_configured": "节点控制台配置已加载",
    "monitor_disabled": "节点控制台未启用；请在 setup 开启后构建并启动",
    "configuration_unavailable": "运行配置未加载成功；请检查启动日志及 CONFIG_FILE",
    "monitor_secret_invalid": "节点控制台会话密钥缺失或无效；请在 setup 保存后构建并启动",
    "monitor_host_mismatch": "Host 与后端发布 URL 不一致；检查访问域名和反向代理 Host",
    "monitor_peer_invalid": "无法识别直连来源 IP",
    "monitor_proxy_untrusted": (
        "直连代理 IP 不在节点控制台的 HTTPS 代理白名单中；"
        "请核对 setup 的宿主机反代网络或 HTTPS 反向代理来源 IP 与 peer_ip 是否一致"
    ),
    "monitor_https_required": "未收到可信 HTTPS 请求；检查反向代理 X-Forwarded-Proto",
    "monitor_loopback_required": "HTTP 控制台仅允许本机回环访问；公网请配置 HTTPS 发布 URL",
    "peer_not_allowed": (
        "前端真实来源 IP 未获允许；核对内部连接 URL、所选 Docker 网络和 internal_auth 来源列表"
    ),
    "token_missing": "缺少 Bearer 令牌；检查前端到后端令牌及反向代理 Authorization",
    "node_not_registered": ("节点尚未登记；请在 /_nodes 选择已保存的前端配对，登记并启用"),
    "node_disabled": "节点已登记但未启用；请在 /_nodes 启用该节点",
    "token_unconfigured": "后端进程未加载该节点的前端到后端令牌；保存后重新创建容器",
    "token_mismatch": "前端到后端令牌不匹配；核对同一节点、同一方向的令牌并重新导入 Base64",
}


def peer_ip(request: Request) -> str:
    try:
        return str(ipaddress.ip_address(request.client.host if request.client else ""))
    except ValueError:
        return "unknown"


def safe_node_id(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) else "invalid"


def access_reason(code: str) -> str | None:
    return _reasons.get(code)


def log_access(
    boundary: AccessBoundary,
    code: str,
    *,
    request: Request | None = None,
    node_id: str | None = None,
    origin: str | None = None,
    allowed_peer_ips: Sequence[str] | None = None,
    trusted_tls_proxy_ips: Sequence[str] | None = None,
) -> None:
    value: dict[str, object] = {"boundary": boundary, "code": code, "reason": _reasons[code]}
    if origin is not None:
        value["origin"] = origin
    for name, items in (
        ("allowed_peer_ips", allowed_peer_ips),
        ("trusted_tls_proxy_ips", trusted_tls_proxy_ips),
    ):
        if items is not None:
            value[name] = list(items)
    if node_id is not None:
        value["node_id"] = safe_node_id(node_id)
    if request is not None:
        value["peer_ip"] = peer_ip(request)
        if boundary == "nodes_monitor":
            host = request.headers.get("host", "")
            value["host"] = host if re.fullmatch(r"[A-Za-z0-9.\[\]:-]{1,255}", host) else "invalid"
            value["scheme"] = "https" if request.url.scheme == "https" else "http"
            forwarded = request.headers.get("x-forwarded-proto", "")
            value["forwarded_proto"] = (
                forwarded
                if forwarded in {"http", "https"}
                else "invalid"
                if forwarded
                else "missing"
            )
    if request is not None:
        signature = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        now = time.monotonic()
        if (last := _recent.get(signature)) is not None and now - last < 30:
            return
        _recent[signature] = now
        _recent.move_to_end(signature)
        if len(_recent) > 256:
            _recent.popitem(last=False)
        app = request.scope.get("app")
        cache = getattr(getattr(app, "state", None), "ip_policy", None)
        policy = (
            cache.current()
            if isinstance(cache, RequestIPCache)
            else (SavedRequestIPPolicy(), False)
        )
        value.update(ip_report(request.scope, *policy))
        value["peer_ip"] = peer_ip(request)
    message = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    level = logging.INFO if code in {"monitor_configured", "monitor_disabled"} else logging.WARNING
    logger.log(level, "[Everunfold access] %s", message)
