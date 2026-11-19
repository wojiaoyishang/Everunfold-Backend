"""Resolve logging addresses from the actual socket and explicitly trusted proxies."""

import ipaddress
import logging
import re
from dataclasses import asdict
from functools import lru_cache
from time import monotonic, perf_counter
from typing import Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.plugins.business_events import BusinessRequest
from app.core.plugins.business_http import BusinessHTTP
from app.core.process_logging import request_log_message, request_log_path

Header = Literal["x-real-ip", "x-forwarded-for", "forwarded", "cf-connecting-ip", "true-client-ip"]
HEADERS: tuple[Header, ...] = (
    "x-real-ip",
    "x-forwarded-for",
    "forwarded",
    "cf-connecting-ip",
    "true-client-ip",
)
CHAINS = {"x-forwarded-for", "forwarded"}
REFRESH_SECONDS = 15
EXPIRY_SECONDS = 60
STORE_KEY = "request_ip_policy"
logger = logging.getLogger("uvicorn.error.everunfold.http")


class RequestIPPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Header | Literal["peer"] = "peer"
    fallback: Header | Literal["peer", "none"] = "peer"
    trusted_proxies: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("trusted_proxies")
    @classmethod
    def validate_proxies(cls, values: list[str]) -> list[str]:
        networks = []
        for value in values:
            network = ipaddress.ip_network(value.strip(), strict=False)
            if isinstance(network, ipaddress.IPv6Network):
                mapped = network.network_address.ipv4_mapped
                if mapped is not None and network.prefixlen >= 96:
                    network = ipaddress.IPv4Network((mapped, network.prefixlen - 96))
            networks.append(str(network))
        return list(dict.fromkeys(networks))


class SavedRequestIPPolicy(RequestIPPolicy):
    revision: int = Field(default=0, ge=0)


class SaveRequestIPPolicy(RequestIPPolicy):
    expected_revision: int = Field(ge=0)


class IPReport(TypedDict):
    peer_ip: str | None
    client_ip: str | None
    client_ip_source: Header | Literal["peer"] | None
    trusted_proxy: bool
    ip_policy_revision: int | None
    x_real_ip: list[str | None]
    x_forwarded_for: list[str | None]
    forwarded: list[str | None]
    cf_connecting_ip: list[str | None]
    true_client_ip: list[str | None]
    x_forwarded_proto: str | None


class RequestIPObservation(BaseModel):
    peer_ip: str | None
    client_ip: str | None
    client_ip_source: Header | Literal["peer"] | None
    trusted_proxy: bool
    ip_policy_revision: int | None
    x_real_ip: list[str | None] = Field(default_factory=list)
    x_forwarded_for: list[str | None] = Field(default_factory=list)
    forwarded: list[str | None] = Field(default_factory=list)
    cf_connecting_ip: list[str | None] = Field(default_factory=list)
    true_client_ip: list[str | None] = Field(default_factory=list)
    x_forwarded_proto: str | None


class RequestIPDiagnosticQuery(BaseModel):
    node_id: str


class RequestIPDiagnostic(BaseModel):
    fastapi: RequestIPObservation
    node_ticket: str | None


class RequestIPCache:
    def __init__(self) -> None:
        self.policy = SavedRequestIPPolicy()
        self.refreshed_at: float = -EXPIRY_SECONDS

    def apply(self, policy: SavedRequestIPPolicy, observed_at: float | None = None) -> None:
        updated = monotonic() if observed_at is None else observed_at
        if updated >= self.refreshed_at:
            self.policy = policy
            self.refreshed_at = updated

    def current(self) -> tuple[SavedRequestIPPolicy, bool]:
        if monotonic() - self.refreshed_at < EXPIRY_SECONDS:
            return self.policy, True
        return SavedRequestIPPolicy(), False


def normalized_ip(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    match = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\](?::[0-9]{1,5})?|([0-9.]+):[0-9]{1,5}", value)
    if match:
        value = match[1] or match[2]
    if len(value) > 45 or "%" in value:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def split_forwarded(value: str, delimiter: str) -> list[str]:
    result = []
    quoted = escaped = False
    start = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == '"' or (quoted and char == "\\"):
            if char == '"':
                quoted = not quoted
            else:
                escaped = True
        elif char == delimiter and not quoted:
            result.append(value[start:index])
            start = index + 1
    return [] if quoted else [*result, value[start:]]


def forwarded_ip(entry: str) -> str | None:
    values = [
        value
        for parameter in split_forwarded(entry, ";")
        for name, separator, value in [parameter.partition("=")]
        if separator and name.strip().lower() == "for"
    ]
    return normalized_ip(values[0]) if len(values) == 1 else None


def header_ips(headers: Headers, name: Header) -> list[str | None]:
    lines = headers.getlist(name)
    if not lines:
        return []
    if sum(map(len, lines)) > 2048:
        return [None]
    if name not in CHAINS:
        return [normalized_ip(lines[0]) if len(lines) == 1 else None]
    joined = ",".join(lines)
    entries = split_forwarded(joined, ",") if name == "forwarded" else joined.split(",")
    if not entries or len(entries) > 16:
        return [None]
    parser = forwarded_ip if name == "forwarded" else normalized_ip
    return [parser(entry) for entry in entries]


@lru_cache(maxsize=8)
def trusted_networks(
    values: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(value) for value in values)


def ip_report(scope: Scope, policy: SavedRequestIPPolicy, policy_available: bool) -> IPReport:
    headers = Headers(scope=scope)
    peer = normalized_ip(scope["client"][0]) if scope.get("client") else None
    reported = {name: header_ips(headers, name) for name in HEADERS}
    networks = trusted_networks(tuple(policy.trusted_proxies))

    def is_trusted(ip: str) -> bool:
        address = ipaddress.ip_address(ip)
        return any(address in network for network in networks)

    trusted = peer is not None and is_trusted(peer)

    def resolve(source: Header | Literal["peer", "none"]) -> str | None:
        if source == "peer":
            return peer
        if source == "none" or not trusted:
            return None
        chain = reported[source]
        if source not in CHAINS:
            return chain[0] if chain else None
        # Work backwards across only known proxies. A malformed hop breaks the chain.
        for hop in reversed(chain):
            if hop is None or not is_trusted(hop):
                return hop
        return None

    sources = (policy.source, policy.fallback)
    selected, selected_source = None, None
    for source in sources:
        if source != "none" and (address := resolve(source)):
            selected, selected_source = address, source
            break
    forwarded_proto = headers.getlist("x-forwarded-proto")
    return {
        "peer_ip": peer,
        "client_ip": selected,
        "client_ip_source": selected_source,
        "trusted_proxy": trusted,
        "ip_policy_revision": policy.revision if policy_available else None,
        "x_real_ip": reported["x-real-ip"],
        "x_forwarded_for": reported["x-forwarded-for"],
        "forwarded": reported["forwarded"],
        "cf_connecting_ip": reported["cf-connecting-ip"],
        "true_client_ip": reported["true-client-ip"],
        "x_forwarded_proto": (
            forwarded_proto[0]
            if len(forwarded_proto) == 1 and forwarded_proto[0] in ("http", "https")
            else None
        ),
    }


class HTTPAccessMiddleware:
    def __init__(
        self, app: ASGIApp, policy: RequestIPCache, business: BusinessHTTP | None = None
    ) -> None:
        self.app = app
        self.policy = policy
        self.business = business

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        log_access = logger.isEnabledFor(logging.INFO)
        business_token = self.business.begin(scope) if self.business is not None else None
        if not log_access and business_token is None:
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        request_id = uuid4().hex
        status: int | None = None
        outcome = "aborted"

        async def observe(message: Message) -> None:
            nonlocal status, outcome
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] in {"http.response.body", "http.response.pathsend"} and not (
                message.get("more_body", False)
            ):
                outcome = "completed"

        try:
            await self.app(scope, receive, observe)
        except Exception:
            outcome = "error"
            if status is None:
                status = 500
            raise
        finally:
            origin = ip_report(scope, *self.policy.current())
            # ASGI keeps the query string separate from the path.
            target = (scope.get("raw_path") or b"").decode("latin-1") or scope["path"]
            user_agent = Headers(scope=scope).get("user-agent", "")
            request = BusinessRequest(
                method=scope["method"],
                request_url=request_log_path(target),
                route=getattr(scope.get("route"), "path", None),
                user_agent=re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", user_agent[:512]).strip() or None,
                duration_ms=round((perf_counter() - started) * 1000, 3),
                client_ip=origin["client_ip"],
                client_ip_source=origin["client_ip_source"],
                peer_ip=origin["peer_ip"],
                trusted_proxy=origin["trusted_proxy"],
                ip_policy_revision=origin["ip_policy_revision"],
            )
            if self.business is not None and business_token is not None:
                self.business.finish(business_token, request_id, status, request)
            if log_access:
                logger.info(
                    request_log_message(
                        request.method,
                        request.request_url,
                        status if status is not None else outcome,
                    ),
                    extra={
                        "http_access": {
                            **asdict(request),
                            **origin,
                            "request_id": request_id,
                            "http_version": scope.get("http_version"),
                            "status": status,
                            "outcome": outcome,
                        }
                    },
                )
