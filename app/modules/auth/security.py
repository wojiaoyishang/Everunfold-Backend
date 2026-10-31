"""Password hashing and domain-separated opaque browser tokens."""

import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
# A dummy hash equalizes the expensive password check for unknown accounts.
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def valid_session_token(token: str | None) -> str | None:
    return token if token is not None and re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else None


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token(token: str) -> str:
    # A separate one-way value: exposing this token never exposes the HttpOnly session.
    return hashlib.sha256(b"everunfold.csrf.v1:" + token.encode("ascii")).hexdigest()


def valid_csrf(token: str, supplied: str | None) -> bool:
    return bool(
        supplied
        and re.fullmatch(r"[a-f0-9]{64}", supplied)
        and hmac.compare_digest(csrf_token(token), supplied)
    )


def normalize_username(username: str) -> str:
    return username.strip().lower()
