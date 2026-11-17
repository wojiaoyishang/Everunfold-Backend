"""Authoritative non-cacheable reader access operation."""

import json
from pathlib import Path

from app.modules.captcha.comments import READ_RECEIPT_TTL
from app.modules.captcha.read_grants import READ_RECEIPT_HEADER
from app.modules.content.access_router import ContentAccessContract

if __name__ == "__main__":
    schema = ContentAccessContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.12.0"
    schema["x-everunfold-reader-authorization"] = {
        "optional_api_key": True,
        "audience": "everunfold",
        "bypass_permission": "captcha:read_bypass",
        "password_bypass": False,
    }
    schema["x-everunfold-read-receipt"] = {
        "header": READ_RECEIPT_HEADER,
        "expires_in": READ_RECEIPT_TTL,
    }
    schema["x-everunfold-operations"] = [
        {
            "name": "readProtectedContent",
            "path": "/v1/reader/access",
            "method": "POST",
            "input": "request",
            "response": "response",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "resumeProtectedArticle",
            "path": "/v1/reader/access/resume",
            "method": "POST",
            "input": "read_target",
            "response": "response",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "readProtectedArticleExcerpt",
            "path": "/v1/reader/access/excerpt",
            "method": "POST",
            "input": "read_target",
            "response": "excerpt_response",
            "csrf": False,
            "credentials": "omit",
        },
    ]
    (Path(__file__).resolve().parents[1] / "contracts/content-access-v1.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
