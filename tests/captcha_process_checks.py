"""P0: real replicas, isolated Redis, durable policy/outbox and gated cache replacement."""

import asyncio
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx2 as httpx
from redis.asyncio import Redis

from app.core.config import load_settings
from app.core.redis_keys import redis_key
from tests.captcha_key_process_checks import check_captcha_keys
from tests.test_auth_http_processes import free_port, uvicorn_process


def check_captcha(
    client: Any,
    csrf: Any,
    api_b: str,
    sql: Any,
    state: Any,
    snapshot: Any,
    wait_for: Any,
    backend_config: Path,
) -> None:
    settings = load_settings(backend_config, environ={})
    assert settings.redis.namespace_scope.startswith("test")
    original = client.get("/v1/admin/captcha").json()["data"]
    rules = {
        "login": True,
        "read": {"article": True, "shuoshuo": True, "page": True},
        "comment": {"article": True, "shuoshuo": True, "page": True},
    }
    origin = client.headers["Origin"]
    cid = str(uuid4())

    def patch(value: Any, **options: Any) -> Any:
        revision = client.get("/v1/admin/captcha").json()["data"]["revision"]
        response = client.patch(
            "/v1/admin/captcha",
            json={
                "expected_revision": revision,
                "provider": "local",
                "rules": value,
                "providers": {},
                **options,
            },
            headers=csrf,
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def published(kind: str, override: str = "inherit") -> Any:
        fields = {
            "title": "CAPTCHA P0 " + kind,
            "body": [{"id": str(uuid4()), "type": "markdown", "content": "CAPTCHA-PRIVATE-" + cid}],
            "settings": {
                "path": "/captcha-" + uuid4().hex,
                "captcha_read": override,
                "captcha_comment": "inherit",
            },
        }
        created = client.post(
            "/v1/admin/content/create",
            json={"type_id": kind, "operation_id": str(uuid4()), "fields": fields},
            headers=csrf,
        )
        assert created.status_code == 200, created.text
        item = created.json()["data"]
        response = client.post(
            "/v1/admin/content/publish",
            json={
                "id": item["id"],
                "expected_revision": item["revision"],
                "operation_id": str(uuid4()),
                "fields": fields,
            },
            headers=csrf,
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def answer(action: str, target: str | None = None) -> Any:
        response = client.post(
            "/v1/reader/captcha/challenge",
            json={"action": action, "content_id": target, "client_id": cid},
            headers={"Origin": origin},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["required"] and data["image"].startswith("data:image/png;base64,")
        assert "no-store" in response.headers["cache-control"]

        # Set a known answer only in this test-owned Redis key. No production test mode exists.
        async def solve() -> None:
            redis = Redis.from_url(settings.redis.url.get_secret_value())
            try:
                key = redis_key(settings, "captcha", "challenge", data["challenge_id"])
                raw = await redis.get(key)
                assert raw
                stored = json.loads(raw)
                stored["digest"] = hashlib.sha256((stored["salt"] + "ABCDE").encode()).hexdigest()
                await redis.set(key, json.dumps(stored), ex=180)
            finally:
                await redis.aclose()

        asyncio.run(solve())
        return {"challenge_id": data["challenge_id"], "client_id": cid, "answer": "abcde"}

    article, exempt, shuoshuo = (
        published("article"),
        published("article", "off"),
        published("shuoshuo"),
    )
    page_id = str(uuid4())
    page = client.post(
        "/v1/admin/pages/save",
        json={
            "id": page_id,
            "expected_revision": 0,
            "operation_id": str(uuid4()),
            "apply": True,
            "value": {
                "title": "CAPTCHA P0 Page",
                "path": "/captcha-page-" + uuid4().hex,
                "enabled": True,
                "html": "<p>PAGE-PRIVATE-" + cid + "</p>",
                "options": {"private": "secret"},
            },
        },
        headers=csrf,
    )
    assert page.status_code == 200, page.text
    try:
        enabled = patch(rules)
        wait_for(lambda: state()["delivery"][0]["status"] == "applied")
        cached = snapshot()
        for row in [article, shuoshuo]:
            masked = next(item for item in cached["contents"] if item["id"] == row["id"])
            assert masked["access"] == "captcha" and not masked["content"] and not masked["excerpt"]
            assert (
                client.post(
                    "/v1/reader/access", json={"id": row["id"]}, headers={"Origin": origin}
                ).status_code
                == 403
            )
        assert next(item for item in cached["contents"] if item["id"] == exempt["id"])["content"]
        masked_page = next(item for item in cached["pages"] if item["id"] == page_id)
        assert (
            masked_page["access"] == "captcha"
            and not masked_page["html"]
            and not masked_page["options"]
        )
        assert "PAGE-PRIVATE-" + cid not in json.dumps(cached)
        assert (
            client.get("/v1/contents", params={"q": "CAPTCHA-PRIVATE-" + cid}).json()["data"][
                "total"
            ]
            == 1
        )
        assert client.get(f"/v1/contents/{article['id']}/comments").status_code == 404
        invalid = client.patch(
            "/v1/admin/captcha",
            json={
                "expected_revision": enabled["revision"] - 1,
                "provider": "local",
                "rules": rules,
                "providers": {},
            },
            headers=csrf,
        )
        assert invalid.status_code == 409
        proof = answer("read", article["id"])

        def read_at(base: str) -> Any:
            return httpx.post(
                base + "/v1/reader/access",
                json={"id": article["id"], "captcha": proof},
                headers={"Origin": origin},
                timeout=10,
            )

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(read_at, [str(client.base_url).rstrip("/"), api_b]))
        assert sorted(result.status_code for result in results) == [200, 403]
        unlocked = next(r.json()["data"] for r in results if r.status_code == 200)
        assert unlocked["content"] and unlocked["read_receipt"]
        for response in results:
            assert "no-store" in response.headers["cache-control"]
        wrong_scope = answer("comment", article["id"])
        assert read_at(api_b).status_code == 403
        assert (
            client.post(
                "/v1/reader/access",
                json={"id": article["id"], "captcha": wrong_scope},
                headers={"Origin": origin},
            ).status_code
            == 403
        )
        stale = answer("read", article["id"])
        patch(rules)
        assert (
            client.post(
                "/v1/reader/access",
                json={"id": article["id"], "captcha": stale},
                headers={"Origin": origin},
            ).status_code
            == 403
        )
        expired = answer("read", article["id"])

        async def expire_challenge() -> None:
            redis = Redis.from_url(settings.redis.url.get_secret_value())
            try:
                await redis.expire(
                    redis_key(settings, "captcha", "challenge", expired["challenge_id"]), 1
                )
                await asyncio.sleep(1.1)
            finally:
                await redis.aclose()

        asyncio.run(expire_challenge())
        assert (
            client.post(
                "/v1/reader/access",
                json={"id": article["id"], "captcha": expired},
                headers={"Origin": origin},
            ).status_code
            == 403
        )
        fresh = answer("read", article["id"])
        unlocked = client.post(
            "/v1/reader/access",
            json={"id": article["id"], "captcha": fresh},
            headers={"Origin": origin},
        ).json()["data"]
        submitted = {
            "operation_id": str(uuid4()),
            "content_id": article["id"],
            "client_id": cid,
            "author": "P0 reader",
            "body": "Pending CAPTCHA P0 comment",
            "read_receipt": unlocked["read_receipt"],
        }
        assert (
            client.post(
                "/v1/reader/comments", json=submitted, headers={"Origin": origin}
            ).status_code
            == 403
        )
        submitted["captcha"] = answer("comment", article["id"])
        first = client.post("/v1/reader/comments", json=submitted, headers={"Origin": origin})
        assert first.status_code == 200 and first.json()["data"]["status"] == "pending", first.text
        repeated = httpx.post(
            api_b + "/v1/reader/comments", json=submitted, headers={"Origin": origin}
        )
        assert repeated.status_code == 200 and repeated.json() == first.json()
        stored = asyncio.run(
            sql(
                "SELECT status FROM reader_comment_submissions WHERE id=$1",
                UUID(submitted["operation_id"]),
            )
        )
        assert len(stored) == 1 and stored[0]["status"] == "pending"
        page_proof = answer("read", page_id)
        page_read = httpx.post(
            api_b + "/v1/reader/access-page",
            json={"id": page_id, "captcha": page_proof},
            headers={"Origin": origin},
        )
        assert page_read.status_code == 200 and "PAGE-PRIVATE-" + cid in page_read.text
        # Approved protected-page comments are returned only after unlock. The same
        # receipt enables reactions on the other API replica, scoped to this page.
        comment_id = uuid4()
        asyncio.run(
            sql(
                "INSERT INTO comments (id,page_id,author,content,created_at,status) "
                "VALUES ($1,$2,'Reader','Protected-page comment',now(),'approved')",
                comment_id,
                UUID(page_id),
            )
        )
        private_page = httpx.post(
            api_b + "/v1/reader/access-page",
            json={"id": page_id, "captcha": answer("read", page_id)},
            headers={"Origin": origin},
        )
        assert private_page.status_code == 200, private_page.text
        unlocked_page = private_page.json()["data"]
        assert any(value["id"] == str(comment_id) for value in unlocked_page["unlocked_comments"])
        grant_headers = {
            "Origin": origin,
            "X-Everunfold-Visitor": str(uuid4()),
            "X-Everunfold-Read-Target": page_id,
            "X-Everunfold-Read-Receipt": unlocked_page["read_receipt"],
        }
        anonymous_headers = {
            key: value for key, value in grant_headers.items() if "Read-" not in key
        }
        assert (
            client.get(
                "/v1/comments/likes", params={"ids": str(comment_id)}, headers=anonymous_headers
            ).json()["data"]["items"]
            == []
        )
        assert (
            client.put(
                f"/v1/comments/{comment_id}/like", json={"liked": True}, headers=anonymous_headers
            ).status_code
            == 404
        )
        liked = client.put(
            f"/v1/comments/{comment_id}/like", json={"liked": True}, headers=grant_headers
        )
        assert liked.status_code == 200 and liked.json()["data"]["count"] == 1, liked.text
        read_like = httpx.get(
            api_b + "/v1/comments/likes", params={"ids": str(comment_id)}, headers=grant_headers
        )
        assert read_like.status_code == 200 and read_like.json()["data"]["items"][0]["liked"]
        assert "no-store" in read_like.headers["cache-control"]
        wrong = {**grant_headers, "X-Everunfold-Read-Target": article["id"]}
        assert (
            client.put(
                f"/v1/comments/{comment_id}/like", json={"liked": False}, headers=wrong
            ).status_code
            == 403
        )

        async def revoke_receipt() -> None:
            redis = Redis.from_url(settings.redis.url.get_secret_value())
            try:
                digest = hashlib.sha256(unlocked_page["read_receipt"].encode()).hexdigest()
                await redis.delete(redis_key(settings, "captcha", "read", digest))
            finally:
                await redis.aclose()

        asyncio.run(revoke_receipt())
        assert (
            client.put(
                f"/v1/comments/{comment_id}/like", json={"liked": False}, headers=grant_headers
            ).status_code
            == 403
        )
        # This matrix ends by disabling the page; run it after the unlocked
        # comment checks, which require the page to remain published.
        check_captcha_keys(
            client, csrf, api_b, sql, patch, rules, article, page_id, settings, answer
        )
        with httpx.Client(base_url=api_b, timeout=10) as anonymous:
            auth = anonymous.get("/v1/auth/session", headers={"Origin": origin}).json()["data"]
            missing = anonymous.post(
                "/v1/auth/login",
                json={"username": "unknown", "password": "irrelevant"},
                headers={"Origin": origin, "X-CSRF-Token": auth["csrf_token"]},
            )
            assert missing.status_code == 403 and missing.json()["code"] == 40386
            login_proof = answer("login")
            passed = anonymous.post(
                "/v1/auth/login",
                json={"username": "unknown", "password": "irrelevant", "captcha": login_proof},
                headers={"Origin": origin, "X-CSRF-Token": auth["csrf_token"]},
            )
            assert passed.status_code == 401 and passed.json()["code"] == 40110
        assert (
            httpx.post(
                api_b + "/v1/reader/captcha/challenge",
                json={"action": "read", "content_id": article["id"], "client_id": cid},
            ).status_code
            == 403
        )
        port = free_port()
        environment = {
            **os.environ,
            "CONFIG_FILE": str(backend_config),
            "REDIS_URL": f"redis://127.0.0.1:{free_port()}/0",
        }
        environment.pop("DATABASE_URL", None)
        with uvicorn_process(port, environment):
            unavailable = f"http://127.0.0.1:{port}/v1/reader/captcha/challenge"
            patch(original["rules"])
            no_requirement = httpx.post(
                unavailable, json={"action": "login", "client_id": cid}, headers={"Origin": origin}
            )
            assert (
                no_requirement.status_code == 200 and not no_requirement.json()["data"]["required"]
            )
            patch(rules)
            failed = httpx.post(
                unavailable,
                json={"action": "login", "client_id": cid},
                headers={"Origin": origin},
                timeout=10,
            )
            assert failed.status_code == 503 and failed.json()["code"] == 50386
    finally:
        patch(
            original["rules"],
            api_key_bypass=original["api_key_bypass"],
            api_key_rate_limit=original["api_key_rate_limit"],
        )
        wait_for(lambda: state()["delivery"][0]["status"] == "applied")
