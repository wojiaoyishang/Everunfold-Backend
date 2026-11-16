"""Private deployment-console fields; credentials stay in the server configuration."""

import re

from pydantic import Field, field_validator, model_validator

from app.core.config import FrontendConfig, validate_http_base
from app.core.schemas import PublicModel
from app.modules.settings.nodes_schemas import NodeChange


class ReceiverConfiguration(PublicModel):
    settings_receiver_url: str = Field(min_length=1, max_length=512)
    allow_private_http: bool = False

    @field_validator("settings_receiver_url")
    @classmethod
    def valid_receiver(cls, value: str) -> str:
        if re.search(r"[\\\s\x00-\x1f\x7f]", value):
            raise ValueError("invalid receiver URL")
        return validate_http_base(value)

    @model_validator(mode="after")
    def valid_transport(self) -> "ReceiverConfiguration":
        FrontendConfig(
            **self.model_dump(),
            nextjs_to_fastapi_token_env="INBOUND_TOKEN",
            fastapi_to_nextjs_token_env="OUTBOUND_TOKEN",
        )
        return self


class DeploymentNodeChange(NodeChange):
    connection: ReceiverConfiguration | None = None

    @model_validator(mode="after")
    def connection_only_on_save(self) -> "DeploymentNodeChange":
        if self.connection is not None and self.action not in {"add", "edit"}:
            raise ValueError("unexpected connection")
        return self


class PairingStatus(PublicModel):
    settings_receiver_url: str | None
    allow_private_http: bool
    inbound_configured: bool
    outbound_configured: bool
