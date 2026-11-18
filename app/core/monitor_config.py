"""Private deployment monitoring settings, resolved before process startup."""

import os
import re
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from urllib.parse import urlsplit

from app.core.config import ConfigurationError, configuration_path
from app.core.deployment_config import DeploymentConfigError, read_deployment


def deployment_environment() -> dict[str, str]:
    env = dict(os.environ)
    try:
        path = configuration_path(environ=env)
        if path.suffix.lower() in {".yaml", ".yml"} and path.is_file():
            return {**read_deployment(path).get("environment", {}), **env}
    except (ConfigurationError, DeploymentConfigError):
        pass
    return env


def endpoint(value: str, name: str) -> str:
    if not value:
        return ""
    url = urlsplit(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError(f"{name} must be an HTTP(S) root URL without credentials")
    _ = url.port
    return value.rstrip("/")


@dataclass(frozen=True)
class MonitoringConfig:
    token: str
    peers: tuple[IPv4Network | IPv6Network, ...]
    frontend: str
    node_token: str
    celery_exporter: str

    def allowed(self, peer: str) -> bool:
        try:
            address = ip_address(peer)
        except ValueError:
            return False
        if isinstance(address, IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self.peers)


def monitoring_config() -> MonitoringConfig | None:
    env = deployment_environment()
    secret = env.get("EVERUNFOLD_METRICS_TOKEN", "")
    if not secret:
        return None
    frontend = endpoint(env.get("EVERUNFOLD_METRICS_FRONTEND_URL", ""), "metrics frontend")
    node_token = env.get("EVERUNFOLD_METRICS_NODE_TOKEN", "")
    if not re.fullmatch(r"[!-~]{32,256}", secret) or (
        frontend and (not re.fullmatch(r"[!-~]{32,256}", node_token) or node_token == secret)
    ):
        raise ValueError("Configure independent monitoring tokens of 32–256 characters")
    peer_text = env.get("EVERUNFOLD_METRICS_ALLOWED_PEERS", "127.0.0.1,::1")
    if "%" in peer_text:
        raise ValueError("Monitoring peers cannot contain interface scopes")
    peers = tuple(
        ip_network(value.strip(), strict=False) for value in peer_text.split(",") if value.strip()
    )
    if not peers or any(network.prefixlen == 0 for network in peers):
        raise ValueError("Configure a restricted metrics IP/CIDR allowlist")
    return MonitoringConfig(
        secret,
        peers,
        frontend,
        node_token,
        endpoint(env.get("EVERUNFOLD_METRICS_CELERY_URL", ""), "metrics celery exporter"),
    )
