"""Versioned, purpose-bound HMAC envelopes; payload secrets never enter URLs or events."""

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from collections.abc import Mapping
from typing import Literal

from app.core.config import AuthConfig
from app.modules.auth.credentials_models import ApiApplication
from app.modules.auth.service import AuthFailure

Direction = Literal["query", "callback"]
PROTOCOL = "EF-AUTH-HMAC-SHA256-V1"
WINDOW_SECONDS = 120


def signing_available(config: AuthConfig) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9_-]{43,128}", os.environ.get(config.application_master_key_env, "")
        )
    )


def application_secret(config: AuthConfig, app_id: str, version: int, direction: Direction) -> str:
    master = os.environ.get(config.application_master_key_env, "")
    if not signing_available(config) or version < 1:
        raise AuthFailure(503, 50360, "应用签名服务尚未配置")
    material = f"everunfold.application.v1\0{app_id}\0{direction}\0{version}".encode()
    return (
        base64.urlsafe_b64encode(hmac.digest(master.encode(), material, "sha256"))
        .decode()
        .rstrip("=")
    )


def key_id(app: ApiApplication, direction: Direction) -> str | None:
    version = app.query_key_version if direction == "query" else app.callback_key_version
    return f"{app.id}:{direction}:{version}" if version else None


def signature_base(
    method: str, url: str, body: bytes, app_id: str, kid: str, timestamp: str, nonce: str
) -> bytes:
    return "\n".join(
        [
            PROTOCOL,
            method.upper(),
            url,
            app_id,
            kid,
            timestamp,
            nonce,
            hashlib.sha256(body).hexdigest(),
        ]
    ).encode()


def signed_headers(
    secret: str,
    app_id: str,
    kid: str,
    method: str,
    url: str,
    body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    request_nonce = secrets.token_urlsafe(24) if nonce is None else nonce
    signature = hmac.new(
        secret.encode(),
        signature_base(method, url, body, app_id, kid, stamp, request_nonce),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Everunfold-App": app_id,
        "X-Everunfold-Key-Id": kid,
        "X-Everunfold-Timestamp": stamp,
        "X-Everunfold-Nonce": request_nonce,
        "X-Everunfold-Signature": signature,
    }


def verify_signature(
    secret: str,
    app_id: str,
    kid: str,
    method: str,
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    now: int | None = None,
) -> str:
    headers = {name.lower(): value for name, value in headers.items()}
    stamp = headers.get("x-everunfold-timestamp", "")
    nonce = headers.get("x-everunfold-nonce", "")
    signature = headers.get("x-everunfold-signature", "")
    valid = (
        headers.get("x-everunfold-app") == app_id
        and headers.get("x-everunfold-key-id") == kid
        and re.fullmatch(r"[0-9]{1,12}", stamp)
        and re.fullmatch(r"[A-Za-z0-9_-]{24,80}", nonce)
        and re.fullmatch(r"[a-f0-9]{64}", signature)
    )
    if not valid or abs((int(time.time()) if now is None else now) - int(stamp)) > WINDOW_SECONDS:
        raise AuthFailure(401, 40160, "应用请求签名无效或已过期")
    expected = hmac.new(
        secret.encode(),
        signature_base(method, url, body, app_id, kid, stamp, nonce),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AuthFailure(401, 40160, "应用请求签名无效或已过期")
    return nonce
