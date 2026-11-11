"""Export the draft presentation contract without touching services or the database."""

import json
from pathlib import Path

from app.modules.content.presentation_schema import ContentPresentation


def main() -> None:
    schema = ContentPresentation.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["x-everunfold-contract-version"] = "0.2.0-preview.5"
    schema["description"] = (
        "Draft presentation contract. No HTTP endpoint or database migration is installed. "
        "Locked records omit body, excerpt and media. Viewer-specific state is separate."
    )
    # Pydantic emits $defs; draft-07 consumers accept named definitions via $ref.
    output = Path(__file__).resolve().parents[1] / "contracts" / "presentation-v0.2.schema.json"
    output.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
