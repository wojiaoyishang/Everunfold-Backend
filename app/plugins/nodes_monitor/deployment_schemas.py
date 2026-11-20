"""Write-only credentials and settings for the private deployment console."""

import re

from pydantic import Field, SecretStr, field_validator, model_validator

from app.core.config import FrontendConfig, validate_http_base
from app.core.schemas import PublicModel
from app.modules.settings.node_registry import NodeBinding
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


class NodeCredentials(PublicModel):
    inbound: SecretStr | None = Field(default=None, min_length=32, max_length=4096)
    outbound: SecretStr | None = Field(default=None, min_length=32, max_length=4096)

    @field_validator("inbound", "outbound")
    @classmethod
    def valid_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(r"[!-~]{32,4096}", value.get_secret_value()):
            raise ValueError("invalid node token")
        return value


class DeploymentNodeChange(NodeChange):
    connection: ReceiverConfiguration | None = None
    credentials: NodeCredentials | None = None
    allowed_peer_ips: list[str] | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("allowed_peer_ips")
    @classmethod
    def valid_peers(cls, values: list[str] | None) -> list[str] | None:
        return NodeBinding.valid_peers(values)

    @model_validator(mode="after")
    def connection_only_on_save(self) -> "DeploymentNodeChange":
        private_fields = {"connection", "credentials", "allowed_peer_ips"}
        if self.action not in {"add", "edit"} and private_fields & self.model_fields_set:
            raise ValueError("unexpected connection")
        return self


class PairingStatus(PublicModel):
    settings_receiver_url: str | None
    allow_private_http: bool
    inbound_configured: bool
    outbound_configured: bool
    allowed_peer_ips: list[str] | None
