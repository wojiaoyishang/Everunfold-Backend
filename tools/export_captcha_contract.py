"""Export provider-neutral CAPTCHA operations; never connects to a database."""

import json
from pathlib import Path

from app.modules.captcha.contract import CaptchaAPIContract


def main() -> None:
    schema = CaptchaAPIContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "1.0.1"
    schema["x-everunfold-operations"] = [
        {
            "name": "readCaptchaSettings",
            "path": "/v1/admin/captcha",
            "method": "GET",
            "response": "settings_response",
            "csrf": False,
            "credentials": "include",
        },
        {
            "name": "saveCaptchaSettings",
            "path": "/v1/admin/captcha",
            "method": "PATCH",
            "input": "save_settings",
            "response": "settings_response",
            "csrf": True,
            "credentials": "include",
        },
        {
            "name": "createCaptchaChallenge",
            "path": "/v1/reader/captcha/challenge",
            "method": "POST",
            "input": "challenge_request",
            "response": "challenge_response",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "readProtectedPage",
            "path": "/v1/reader/access-page",
            "method": "POST",
            "input": "page_request",
            "response": "page_response",
            "csrf": False,
            "credentials": "omit",
        },
        {
            "name": "submitReaderComment",
            "path": "/v1/reader/comments",
            "method": "POST",
            "input": "comment_request",
            "response": "comment_response",
            "csrf": False,
            "credentials": "omit",
        },
    ]
    (Path(__file__).resolve().parents[1] / "contracts/captcha-v1.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
