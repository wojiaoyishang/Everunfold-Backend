"""Normalize the identifiers explicitly permitted in business observation."""

import re


def email_domain(address: str) -> str:
    local, separator, domain = address.strip().rpartition("@")
    if not local or not separator or not 0 < len(domain) <= 253:
        return ""
    try:
        normalized = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    if len(normalized) > 253 or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in normalized.split(".")
    ):
        return ""
    return normalized


def login_identifier(value: object) -> str:
    if not isinstance(value, str) or len(value) > 254:
        return ""
    normalized = value.strip().lower()
    if "@" in normalized:
        domain = email_domain(normalized)
        return f"***@{domain}" if domain else ""
    return normalized if re.fullmatch(r"[a-z0-9_.-]{1,64}", normalized) else ""
