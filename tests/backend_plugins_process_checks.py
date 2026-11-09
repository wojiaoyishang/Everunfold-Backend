"""Two APIs, explicit deployment and fresh Node readers agree on backend contributions."""

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2 as httpx


def check_backend_plugins(
    client: Any,
    csrf: Any,
    api_b: str,
    config: Path,
    env: dict[str, str],
    wait_for: Any,
    frontend: Path,
    node: str,
    epoch: str,
    directory: Path,
    node_executable: str,
    node_env: dict[str, str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "tools.register_backend_plugins",
        "--config",
        str(config),
        "--apply",
    ]

    def deploy() -> None:
        result = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 0, result.stderr

    def state() -> Any:
        return client.get("/v1/admin/settings").json()["data"]

    before = state()
    deploy()
    current = state()
    assert current["revision"] == before["revision"] + 1
    deploy()
    assert state()["revision"] == current["revision"]
    wait_for(lambda: state()["delivery"][0]["status"] == "applied")
    peer = httpx.get(api_b + "/v1/admin/extensions", cookies=client.cookies)
    assert peer.status_code == 200 and peer.json()["data"]["backend_in_sync"]
    assert {p["id"] for p in peer.json()["data"]["backend_plugins"]} >= {
        "example.cards",
        "core.basic-shortcodes",
    }
    catalog = client.get("/v1/admin/shortcodes").json()["data"]
    assert any(
        c["source"]["name"] == "example:card" and not c["editable"] for c in catalog["items"]
    )
    assert catalog["applied"] == current["value"]["shortcodes"]

    def render() -> Any:
        result = subprocess.run(
            [
                node_executable,
                "--import",
                "tsx",
                "--import",
                "./tools/extensions/node.mjs",
                "tools/fixtures/shortcodes-probe.mts",
                str(directory),
                node,
                epoch,
                "example:card",
            ],
            cwd=frontend,
            env=node_env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    rendered = render()
    assert not rendered["diagnostics"] and "From applied snapshot" in rendered["html"]
    assert "<h3" in rendered["html"] and "P0 body" in rendered["html"], rendered["html"]
    spoof = copy.deepcopy(current["value"])
    spoof["shortcodes"][0]["html"] = "spoofed"
    assert (
        client.patch(
            "/v1/admin/settings",
            json={"expected_revision": current["revision"], "value": spoof},
            headers=csrf,
        ).status_code
        == 422
    )

    def toggle(disabled: list[str]) -> Any:
        current = state()
        value = copy.deepcopy(current["value"])
        value["extensions"]["disabled_plugins"] = disabled
        result = client.patch(
            "/v1/admin/settings",
            json={"expected_revision": current["revision"], "value": value},
            headers=csrf,
        )
        assert result.status_code == 200, result.text
        wait_for(lambda: state()["delivery"][0]["status"] == "applied")
        return result.json()["data"]

    original_disabled = current["value"]["extensions"]["disabled_plugins"]
    toggle([*original_disabled, "example.cards"])
    assert not any(
        c["source"]["name"] == "example:card"
        for c in client.get("/v1/admin/shortcodes").json()["data"]["items"]
    )
    disabled_render = render()
    assert "example:card" in disabled_render["html"]
    assert "<h3" not in disabled_render["html"]
    toggle(original_disabled)
    assert not render()["diagnostics"]
    assert state()["value"] == current["value"]
    # Real selected provider: its disabled backend cannot issue challenges, even
    # with valid-looking public configuration. Never contact the external verifier.
    captcha = client.get("/v1/admin/captcha").json()["data"]
    selected = client.patch(
        "/v1/admin/captcha",
        headers=csrf,
        json={
            "expected_revision": captcha["revision"],
            "provider": "google.recaptcha-v2",
            "rules": {**captcha["rules"], "login": True},
            "providers": {
                "google.recaptcha-v2": {
                    "site_key": "test-only",
                    "secret": "test-only",
                    "hostnames": ["example.test"],
                }
            },
        },
    )
    assert selected.status_code == 200, selected.text
    usage = client.post(
        "/v1/admin/extensions/usage", headers=csrf, json={"plugin_ids": ["core.captcha-google"]}
    )
    assert usage.json()["data"]["plugins"][0]["captcha_references"] == 1
    toggle([*original_disabled, "core.captcha-google"])
    challenge = client.post(
        "/v1/reader/captcha/challenge", json={"action": "login", "client_id": str(uuid4())}
    )
    assert challenge.status_code == 503, challenge.text
    restored = client.patch(
        "/v1/admin/captcha",
        headers=csrf,
        json={
            "expected_revision": selected.json()["data"]["revision"],
            "provider": captcha["provider"],
            "rules": captcha["rules"],
            "providers": {},
            "remove_providers": ["google.recaptcha-v2"],
            "api_key_bypass": captcha["api_key_bypass"],
            "api_key_rate_limit": captcha["api_key_rate_limit"],
        },
    )
    assert restored.status_code == 200, restored.text
    toggle(original_disabled)
