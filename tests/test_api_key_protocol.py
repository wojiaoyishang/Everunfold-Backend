"""Published service schema and purpose/URL/nonce-bound proofs."""

import json
from pathlib import Path

import pytest

from app.modules.auth.application_signatures import signed_headers, verify_signature
from app.modules.auth.service import AuthFailure
from tools.export_api_key_protocol import build_schema


def test_published_api_key_protocol() -> None:
    assert (
        json.loads(Path("contracts/api-key-protocol-v1.schema.json").read_text()) == build_schema()
    )


@pytest.mark.parametrize(
    "method,url,body,kid",
    [
        ("RESPONSE", "http://child.invalid/callback", b"{}", "demo:callback:1"),
        ("POST", "http://child.invalid/other", b"{}", "demo:callback:1"),
        ("POST", "http://child.invalid/callback", b"{ }", "demo:callback:1"),
        ("POST", "http://child.invalid/callback", b"{}", "demo:query:1"),
    ],
)
def test_signature_never_transfers_between_directions_and_targets(
    method: str, url: str, body: bytes, kid: str
) -> None:
    proof = signed_headers(
        "test-secret", "demo", "demo:callback:1", "POST", "http://child.invalid/callback", b"{}"
    )
    with pytest.raises(AuthFailure):
        verify_signature("test-secret", "demo", kid, method, url, body, proof)
