"""Deployment-owned operations page, mounted only by the API entry point."""

import os
import re

from fastapi import FastAPI

from app.core.access_diagnostics import log_access
from app.core.config import Settings


def mount_nodes_monitor(application: FastAPI, settings: Settings | None) -> None:
    from .admin_router import router as admin_router

    application.include_router(admin_router)
    if settings is None:
        log_access("nodes_monitor", "configuration_unavailable")
        return
    if not settings.nodes_monitor.enabled:
        log_access(
            "nodes_monitor",
            "monitor_disabled",
            allowed_peer_ips=settings.internal_auth.allowed_peer_ips,
        )
        return
    from .routes import monitor_router
    from .security import MonitorAccess

    config = settings.nodes_monitor
    origin = settings.nodes_monitor_origin
    assert origin is not None  # Enforced by Settings when the console is enabled.
    secret = os.environ.get(config.session_secret_env, "")
    access = None
    if re.fullmatch(r"[A-Za-z0-9_-]{43,128}", secret) and config.password_hash:
        access = MonitorAccess(
            origin,
            config.password_hash.get_secret_value(),
            secret,
            config.session_seconds,
            config.trusted_tls_proxy_ips,
        )
    log_access(
        "nodes_monitor",
        "monitor_configured" if access is not None else "monitor_secret_invalid",
        origin=origin,
        allowed_peer_ips=settings.internal_auth.allowed_peer_ips,
        trusted_tls_proxy_ips=config.trusted_tls_proxy_ips,
    )
    application.include_router(monitor_router(access))
