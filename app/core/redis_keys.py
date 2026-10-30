"""Single entrypoint for project-owned Redis keys and channels."""

from app.core.config import Settings


def redis_key(settings: Settings, *parts: str) -> str:
    if not parts or any(not part or any(char in part for char in "*?[]\r\n") for part in parts):
        raise ValueError("Redis key parts must be nonempty literal identifiers")
    return settings.redis_prefix + ":".join(parts)


def redis_channel(settings: Settings, *parts: str) -> str:
    return redis_key(settings, *parts)
