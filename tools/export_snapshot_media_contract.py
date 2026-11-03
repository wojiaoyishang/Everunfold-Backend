"""Refresh the public snapshot's media descriptor from the authoritative DTO."""

import json
from pathlib import Path

from app.modules.content.schemas import MediaURL, PublicSnapshot


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "contracts/public-v0.1.schema.json"
    schema = json.loads(target.read_text(encoding="utf-8"))
    media = MediaURL.model_json_schema(ref_template="#/definitions/{model}")
    schema["definitions"].update(media.pop("$defs", {}))
    schema["definitions"]["ContentDetail"]["properties"]["media_urls"]["additionalProperties"] = (
        media
    )
    schema["properties"]["schema_version"] = PublicSnapshot.model_json_schema()["properties"][
        "schema_version"
    ]
    schema["x-everunfold-contract-version"] = "0.5.0"
    schema["description"] = "公开阅读快照 0.5；按内容引用提供字体描述，兼容已有快照。"
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
