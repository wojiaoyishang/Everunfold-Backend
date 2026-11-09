"""Invocation JSON remains intact while public defaults omit sensitive fields."""

import json
from dataclasses import dataclass
from typing import Annotated, TypedDict

import pytest
from pydantic import BaseModel, Field, SecretBytes, SecretStr

from app.core.jobs.definitions import TaskDefinition
from app.core.jobs.parameter_metadata import public_parameters
from app.core.jobs.parameters import checked_json, normalized_parameters, parameters_schema


class SensitiveOptions(BaseModel):
    host: str = "example.test"
    password: str = Field(default="private-password", json_schema_extra={"writeOnly": True})
    default: str = Field(default="private-default", json_schema_extra={"format": "password"})


class SensitiveParameters(BaseModel):
    account: str = "public-label"
    token: str = Field(default="private-token", json_schema_extra={"format": "password"})
    nested: SensitiveOptions = SensitiveOptions()
    rows: list[SensitiveOptions] = [SensitiveOptions()]


def test_public_defaults_and_schema_hide_sensitive_values_without_mutating_execution():
    spec = TaskDefinition(
        "example.privacy",
        "Privacy",
        "app.core.jobs.handlers:settings",
        parameters=SensitiveParameters,
    )
    schema, public = public_parameters(parameters_schema(spec), spec.default_parameters)
    encoded = json.dumps({"schema": schema, "defaults": public})
    for private in ("private-token", "private-password", "private-default"):
        assert private not in encoded
    assert public == {
        "account": "public-label",
        "nested": {"host": "example.test"},
        "rows": [{"host": "example.test"}],
    }
    private = normalized_parameters(spec, {})
    assert private["token"] == "private-token"
    assert private["nested"]["password"] == "private-password"
    assert private["rows"][0]["default"] == "private-default"
    assert spec.default_parameters == private


@pytest.mark.parametrize("secret", [SecretStr, SecretBytes])
def test_secret_model_types_are_rejected_recursively_instead_of_sending_masks(secret):
    class Nested(BaseModel):
        value: secret

    class Parameters(BaseModel):
        nested: list[Annotated[Nested | None, Field()]]

    with pytest.raises(ValueError, match="SecretStr/SecretBytes"):
        TaskDefinition(
            "example.secret",
            "Secret",
            "app.core.jobs.handlers:settings",
            parameters=Parameters,
            default_parameters={"nested": [{"value": "private-secret"}]},
        )


def test_canonical_argument_comparison_preserves_types_and_ignores_dictionary_order():
    assert checked_json({"nested": {"a": 1, "b": 2}, "flag": True}) == checked_json(
        {"flag": True, "nested": {"b": 2, "a": 1}}
    )
    assert checked_json({"value": True}) != checked_json({"value": 1})


@dataclass
class SecretDataclass:
    token: SecretStr


class SecretMapping(TypedDict):
    token: SecretBytes


@pytest.mark.parametrize("nested_type", [SecretDataclass, SecretMapping])
def test_secret_dataclass_and_mapping_fields_are_rejected(nested_type):
    class Parameters(BaseModel):
        nested: nested_type

    with pytest.raises(ValueError, match="SecretStr/SecretBytes"):
        TaskDefinition(
            "example.nested-secret",
            "Nested secret",
            "app.core.jobs.handlers:settings",
            parameters=Parameters,
        )
