"""Persisted grant templates, immutable provenance and real account-boundary checks."""

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.database import Database
from app.modules.auth.accounts_schemas import ProfileFields
from app.modules.auth.preset_models import UserPermissionPreset
from tests.test_accounts import PASSWORD, TEMPORARY, AccountsTest, create_user, headers, signed_in
from tests.test_accounts import accounts as accounts_fixture

accounts = accounts_fixture


@pytest.fixture
def presets(accounts: AccountsTest) -> Iterator[AccountsTest]:
    yield accounts

    async def clean() -> None:
        db = Database(accounts.settings.database.url.get_secret_value())
        try:
            async with db.session_factory() as session:
                await session.execute(
                    delete(UserPermissionPreset).where(
                        UserPermissionPreset.name.startswith(accounts.name)
                    )
                )
                await session.commit()
        finally:
            await db.close()

    asyncio.run(clean())


def fields(user: dict[str, Any]) -> dict[str, Any]:
    return {name: user[name] for name in ProfileFields.model_fields if name in user}


def new_preset(ctx: AccountsTest, **grants: str) -> dict[str, Any]:
    response = ctx.root.post(
        "/v1/admin/user-presets",
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "current_password": PASSWORD,
            "value": {
                "name": ctx.name + "预设" + uuid4().hex[:8],
                "grants": {"everunfold": grants},
            },
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json()["data"])


def test_preset_crud_application_keeps_existing_users_and_legacy_retries(
    presets: AccountsTest,
) -> None:
    ctx = presets
    preset = new_preset(ctx, **{"content:read": "all", "content:write": "own"})
    body = {
        "operation_id": str(uuid4()),
        "username": ctx.name + "_preset",
        "display_name": "预设成员",
        "password": TEMPORARY,
        "preset": {"id": preset["id"], "expected_revision": preset["revision"]},
        "grants": {"everunfold": {"users:manage": "all"}},
    }
    created = ctx.root.post("/v1/admin/users", json=body, headers=headers(ctx.root))
    assert created.status_code == 201, created.text
    user = created.json()["data"]
    assert user["grants"] == preset["grants"] and "users:manage" not in user["grants"]["everunfold"]
    assert user["permission_preset"] == {"id": preset["id"], "name": preset["name"], "revision": 1}
    changed = {
        "operation_id": str(uuid4()),
        "expected_revision": 1,
        "current_password": PASSWORD,
        "value": {
            "name": preset["name"] + "新版",
            "description": "updated",
            "grants": {"everunfold": {"content:read": "own"}},
        },
    }
    response = ctx.root.patch(
        "/v1/admin/user-presets/" + preset["id"], json=changed, headers=headers(ctx.root)
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revision"] == 2
    assert (
        ctx.root.post("/v1/admin/users", json=body, headers=headers(ctx.root)).json()["data"]
        == user
    )
    assert ctx.root.get("/v1/admin/users/" + user["id"]).json()["data"] == user
    stale = ctx.root.patch(
        "/v1/admin/users/" + user["id"],
        headers=headers(ctx.root),
        json={
            "operation_id": str(uuid4()),
            "expected_revision": user["revision"],
            "value": fields(user),
            "role": "user",
            "active": True,
            "preset": body["preset"],
        },
    )
    assert stale.status_code == 409 and stale.json()["code"] == 40970
    profile_only = {
        "operation_id": str(uuid4()),
        "expected_revision": user["revision"],
        "value": fields(user),
        "role": "user",
        "active": True,
        "grants": user["grants"],
    }
    saved = ctx.root.patch(
        "/v1/admin/users/" + user["id"], json=profile_only, headers=headers(ctx.root)
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["permission_preset"] == user["permission_preset"]
    assert (
        ctx.root.patch(
            "/v1/admin/users/" + user["id"], json=profile_only, headers=headers(ctx.root)
        ).json()
        == saved.json()
    )
    removed = {"operation_id": str(uuid4()), "expected_revision": 2, "current_password": PASSWORD}
    result = ctx.root.post(
        "/v1/admin/user-presets/" + preset["id"] + "/remove",
        json=removed,
        headers=headers(ctx.root),
    )
    assert result.status_code == 200 and all(
        p["id"] != preset["id"] for p in result.json()["data"]["items"]
    )
    assert (
        ctx.root.post(
            "/v1/admin/user-presets/" + preset["id"] + "/remove",
            json=removed,
            headers=headers(ctx.root),
        ).status_code
        == 200
    )
    latest = ctx.root.get("/v1/admin/users/" + user["id"]).json()["data"]
    assert latest["grants"] == user["grants"] and latest["auth_version"] == user["auth_version"]
    assert latest["permission_preset"] == user["permission_preset"]
    custom = {
        **profile_only,
        "operation_id": str(uuid4()),
        "expected_revision": latest["revision"],
        "preset": None,
    }
    cleared = ctx.root.patch(
        "/v1/admin/users/" + user["id"], json=custom, headers=headers(ctx.root)
    )
    assert cleared.status_code == 200 and cleared.json()["data"]["permission_preset"] is None
    body.update(operation_id=str(uuid4()), username=ctx.name + "_deleted")
    assert ctx.root.post("/v1/admin/users", json=body, headers=headers(ctx.root)).status_code == 404


def test_preset_mutations_validate_password_names_and_registered_scopes(
    presets: AccountsTest,
) -> None:
    ctx = presets
    template: dict[str, Any] = {
        "operation_id": str(uuid4()),
        "current_password": "wrong-password",
        "value": {"name": ctx.name + "检查", "grants": {}},
    }
    wrong = ctx.root.post("/v1/admin/user-presets", json=template, headers=headers(ctx.root))
    assert wrong.status_code == 403 and wrong.json()["code"] == 40361
    template.update(current_password=PASSWORD)
    template["value"]["grants"] = {"everunfold": {"not:registered": "all"}}
    assert (
        ctx.root.post(
            "/v1/admin/user-presets", json=template, headers=headers(ctx.root)
        ).status_code
        == 422
    )
    template["value"]["grants"] = {"everunfold": {"media:read": "own"}}
    assert (
        ctx.root.post(
            "/v1/admin/user-presets", json=template, headers=headers(ctx.root)
        ).status_code
        == 422
    )
    template["value"]["grants"] = {}
    result = ctx.root.post("/v1/admin/user-presets", json=template, headers=headers(ctx.root))
    assert result.status_code == 201
    assert PASSWORD not in result.text
    template.update(operation_id=str(uuid4()))
    duplicate = ctx.root.post("/v1/admin/user-presets", json=template, headers=headers(ctx.root))
    assert duplicate.status_code == 409 and duplicate.json()["code"] == 40971
    preset = result.json()["data"]
    remove = {"operation_id": str(uuid4()), "current_password": PASSWORD, "expected_revision": 1}
    no_csrf = ctx.root.post(
        "/v1/admin/user-presets/" + preset["id"] + "/remove",
        json=remove,
        headers={"Origin": "http://127.0.0.1:3000"},
    )
    assert no_csrf.status_code == 403
    bearer = ctx.root.get("/v1/admin/user-presets", headers={"Authorization": "Bearer invalid"})
    assert bearer.status_code == 403 and bearer.json()["code"] == 40362


def test_delegated_manager_cannot_apply_or_manage_a_preset_above_own_ceiling(
    presets: AccountsTest,
) -> None:
    ctx = presets
    lower = new_preset(ctx, **{"content:read": "own"})
    upper = new_preset(ctx, **{"content:read": "all"})
    manager = create_user(ctx)
    updated = ctx.root.patch(
        "/v1/admin/users/" + manager["id"],
        json={
            "operation_id": str(uuid4()),
            "expected_revision": manager["revision"],
            "value": fields(manager),
            "role": "user",
            "active": True,
            "grants": {"everunfold": {"users:manage": "all", "content:read": "own"}},
        },
        headers=headers(ctx.root),
    )
    assert updated.status_code == 200
    member = ctx.client()
    signed_in(member, manager["username"], TEMPORARY)
    changed = member.post(
        "/v1/account/password",
        json={
            "operation_id": str(uuid4()),
            "expected_revision": updated.json()["data"]["revision"],
            "current_password": TEMPORARY,
            "new_password": PASSWORD,
        },
        headers=headers(member),
    )
    assert changed.status_code == 200, changed.text
    catalog = member.get("/v1/admin/user-presets").json()["data"]
    assert not catalog["can_manage"]
    assert lower["id"] in {p["id"] for p in catalog["items"]}
    assert upper["id"] not in {p["id"] for p in catalog["items"]}
    attempted = member.post(
        "/v1/admin/user-presets",
        json={
            "operation_id": str(uuid4()),
            "current_password": PASSWORD,
            "value": {"name": ctx.name + "越权", "grants": {}},
        },
        headers=headers(member),
    )
    assert attempted.status_code == 403 and attempted.json()["code"] == 40370
    create = {
        "operation_id": str(uuid4()),
        "username": ctx.name + "_limited",
        "display_name": "受限",
        "password": TEMPORARY,
        "preset": {"id": upper["id"], "expected_revision": 1},
    }
    assert member.post("/v1/admin/users", json=create, headers=headers(member)).status_code == 403
    create.update(operation_id=str(uuid4()), preset={"id": lower["id"], "expected_revision": 1})
    accepted = member.post("/v1/admin/users", json=create, headers=headers(member))
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["data"]["grants"] == lower["grants"]
