"""Node membership is shared by backend processes; only deployment can bind a receiver."""

import re
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field, TypeAdapter, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import FrontendConfig, Settings, validate_http_base
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


_registrations = TypeAdapter(dict[str, NodeRegistration])
_bindings = TypeAdapter(dict[str, FrontendConfig])


@dataclass(frozen=True)
class NodeDirectory:
    revision: int
    entries: dict[str, NodeRegistration]
    bindings: dict[str, FrontendConfig]

    @property
    def targets(self) -> dict[str, FrontendConfig]:
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
        config.frontends,
    )


def registry_directory(record: FrontendNodeRegistry) -> NodeDirectory:
    return NodeDirectory(
        record.revision,
        _registrations.validate_python(record.nodes),
        _bindings.validate_python(record.bindings),
    )


def registration_bindings(directory: NodeDirectory, config: Settings) -> dict[str, FrontendConfig]:
    """Offer the saved setup pairing during explicit console registration."""
    pairing = config.setup_frontend
    if pairing is None:
        return directory.bindings
    node_id, binding = pairing
    return {node_id: binding, **directory.bindings}


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


async def read_targets(session: AsyncSession, config: Settings) -> dict[str, FrontendConfig]:
    return (await read_directory(session, config)).targets
