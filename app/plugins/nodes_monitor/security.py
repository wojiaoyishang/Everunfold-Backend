"""Independent password and short-lived browser sessions for the node monitor."""

import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import deque
from urllib.parse import urlsplit

from anyio import CapacityLimiter, to_thread
from fastapi import Request

from app.core.config import is_loopback_host
from app.modules.auth.security import verify_password

COOKIE_NAME = "everunfold_nodes"
LOGIN_WINDOW = 300
_SESSION = re.compile(r"([0-9]{1,12}\.[A-Za-z0-9_-]{22})\.([a-f0-9]{64})")


class MonitorAccess:
    def __init__(self, origin: str, password_hash: str, secret: str, lifetime: int) -> None:
        url = urlsplit(origin)
        self.secure = url.scheme == "https"
        self.host = url.netloc.lower()
        if url.port == (443 if self.secure else 80):
            self.host = self.host.rsplit(":", 1)[0]
        self.origin = f"{url.scheme}://{self.host}"
        self.password_hash = password_hash
        # Changing either credential also invalidates existing monitor sessions.
        self.key = hmac.digest(
            secret.encode("ascii"),
            f"everunfold.nodes.v1\0{self.origin}\0{password_hash}".encode(),
            "sha256",
        )
        self.lifetime = lifetime
        self.attempts: deque[tuple[float, str]] = deque()
        self.limiter = CapacityLimiter(2)

    def accepts_host(self, request: Request) -> bool:
        if request.headers.get("host", "").lower() != self.host:
            return False
        if self.secure:
            return request.url.scheme == "https"
        peer = request.client.host if request.client else ""
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return (
            request.url.scheme == "http"
            and address.is_loopback
            and is_loopback_host(urlsplit(self.origin).hostname or "")
        )

    def accepts_post(self, request: Request) -> bool:
        return request.headers.get("origin") == self.origin

    def allow_attempt(self, peer: str) -> bool:
        now = time.monotonic()
        while self.attempts and self.attempts[0][0] <= now - LOGIN_WINDOW:
            self.attempts.popleft()
        if len(self.attempts) >= 40 or sum(value == peer for _, value in self.attempts) >= 8:
            return False
        self.attempts.append((now, peer))
        return True

    async def verify(self, password: str) -> bool:
        return await to_thread.run_sync(
            verify_password, self.password_hash, password, limiter=self.limiter
        )

    def signature(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        payload = f"{int(time.time()) + self.lifetime}.{secrets.token_urlsafe(16)}"
        return f"{payload}.{self.signature(payload)}"

    def authenticated(self, request: Request) -> bool:
        value = request.cookies.get(COOKIE_NAME, "")
        match = _SESSION.fullmatch(value)
        if not match:
            return False
        payload, signature = match.groups()
        return int(payload.split(".", 1)[0]) > time.time() and hmac.compare_digest(
            self.signature(payload), signature
        )
