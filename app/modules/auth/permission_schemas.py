"""Shared descriptions for registered user and machine permission choices."""

from app.core.schemas import PublicModel


class PermissionDefinition(PublicModel):
    code: str
    label: str
    api_key: bool
    owner_scope: bool
