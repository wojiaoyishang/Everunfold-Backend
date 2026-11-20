"""Shared node membership, receiver bindings and deployment credential overrides."""

import base64
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import FrontendConfig, InternalAuthConfig, Settings, validate_http_base
from app.core.schemas import PublicModel
from app.modules.settings.models import FrontendNodeRegistry

NODE_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
MAX_NODES = 128
NodeId = Annotated[str, Field(pattern=NODE_ID)]


class NodeRegistration(PublicModel):
    base_url: str = Field(default="", max_length=512)
    enabled: bool

    @field_validator("base_url")
    @classmethod
    def valid_public_url(cls, value: str) -> str:
        if re.search(r"[\\\s\x00-\x1f\x7f]", value):
            raise ValueError("invalid URL characters")
        return validate_http_base(value) if value else ""


Direction = Literal["inbound", "outbound"]


class NodeBinding(FrontendConfig):
    allowed_peer_ips: list[str] | None = Field(default=None, max_length=32)
    encrypted_inbound: str | None = Field(default=None, repr=False)
    encrypted_outbound: str | None = Field(default=None, repr=False)

    @field_validator("allowed_peer_ips")
    @classmethod
    def valid_peers(cls, values: list[str] | None) -> list[str] | None:
        return InternalAuthConfig.valid_networks(values) if values is not None else None

    def token(
        self,
        settings: Settings,
        node_id: str,
        direction: Direction,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> str:
        env = os.environ if environment is None else environment
        if direction == "inbound":
            ciphertext, reference = self.encrypted_inbound, self.nextjs_to_fastapi_token_env
        else:
            ciphertext, reference = self.encrypted_outbound, self.fastapi_to_nextjs_token_env
        if ciphertext is None:
            return env.get(reference, "")
        try:
            return (
                node_cipher(settings, node_id, direction, environment=env)
                .decrypt(ciphertext.encode())
                .decode()
            )
        except (InvalidToken, ValueError):
            # An unreadable override must never revive a superseded environment token.
            return ""


def node_cipher(
    settings: Settings,
    node_id: str,
    direction: Direction,
    *,
    environment: Mapping[str, str] | None = None,
) -> Fernet:
    env = os.environ if environment is None else environment
    master = env.get(settings.auth.application_master_key_env, "")
    if not 43 <= len(master) <= 256:
        raise ValueError("后端应用主密钥尚未配置")
    context = f"everunfold.frontend-node.v1:{node_id}:{direction}"
    return Fernet(
        base64.urlsafe_b64encode(hmac.digest(master.encode(), context.encode(), "sha256"))
    )


def deployed_binding(config: FrontendConfig) -> NodeBinding:
    return NodeBinding.model_validate(config.model_dump())


_registrations = TypeAdapter(dict[str, NodeRegistration])
_bindings = TypeAdapter(dict[str, NodeBinding])


@dataclass(frozen=True)
class NodeDirectory:
    revision: int
    entries: dict[str, NodeRegistration]
    bindings: dict[str, NodeBinding]

    @property
    def targets(self) -> dict[str, NodeBinding]:
        return {
            node_id: self.bindings[node_id].model_copy(update=entry.model_dump())
            for node_id, entry in self.entries.items()
            if node_id in self.bindings
        }

    @property
    def receivers(self) -> set[str]:
        return {
            node_id
            for node_id, target in self.targets.items()
            if target.enabled and target.settings_receiver_url
        }


def deployment_directory(config: Settings) -> NodeDirectory:
    return NodeDirectory(
        0,
        {
            node_id: NodeRegistration(base_url=node.base_url, enabled=node.enabled)
            for node_id, node in config.frontends.items()
        },
        {node_id: deployed_binding(node) for node_id, node in config.frontends.items()},
    )


def registry_directory(record: FrontendNodeRegistry) -> NodeDirectory:
    return NodeDirectory(
        record.revision,
        _registrations.validate_python(record.nodes),
        _bindings.validate_python(record.bindings),
    )


def registration_bindings(directory: NodeDirectory, config: Settings) -> dict[str, NodeBinding]:
    """Offer the saved setup pairing during explicit console registration."""
    pairing = config.setup_frontend
    if pairing is None:
        return directory.bindings
    node_id, binding = pairing
    return {node_id: deployed_binding(binding), **directory.bindings}


async def registry_record(
    session: AsyncSession, *, lock: bool = False
) -> FrontendNodeRegistry | None:
    # Re-read even when the ORM session has a previously loaded instance.
    query = (
        select(FrontendNodeRegistry)
        .where(FrontendNodeRegistry.id == 1)
        .execution_options(populate_existing=True)
    )
    return await session.scalar(query.with_for_update() if lock else query)


async def read_directory(session: AsyncSession, config: Settings) -> NodeDirectory:
    record = await registry_record(session)
    return registry_directory(record) if record else deployment_directory(config)


async def read_targets(session: AsyncSession, config: Settings) -> dict[str, NodeBinding]:
    return (await read_directory(session, config)).targets
