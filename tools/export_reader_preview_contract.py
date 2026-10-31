"""Export the demo reader contract and operation metadata; does not connect to storage."""

import json
from pathlib import Path

from app.modules.content.reader_preview_schema import ReaderPreviewContract


def main() -> None:
    schema = ReaderPreviewContract.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "0.1.0-preview.1"
    schema["description"] = (
        "Opt-in synthetic data only. No authentication, authorization or writes."
    )
    schema["x-everunfold-operations"] = [
        {
            "name": "readPreviewIdentity",
            "path": "/v1/reader-preview/identity",
            "query": "identity_query",
            "response": "identity",
        },
        {
            "name": "readPreviewMetrics",
            "path": "/v1/reader-preview/metrics",
            "query": "metrics_query",
            "response": "metrics",
        },
    ]
    target = Path(__file__).resolve().parents[1] / "contracts" / "reader-preview-v0.1.schema.json"
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
