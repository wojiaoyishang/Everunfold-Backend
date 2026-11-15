"""Export public business OpenAPI without connecting to runtime dependencies."""

import argparse
import json
from pathlib import Path

from app.entrypoints.api import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_VERSION = "0.1.1"


def export_openapi(output: Path) -> None:
    schema = create_app().openapi()
    schema["paths"] = {
        path: operations for path, operations in schema["paths"].items() if path.startswith("/v1/")
    }
    schema["x-everunfold-contract-version"] = CONTRACT_VERSION
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_suffix(output.suffix + ".tmp")
    staging.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    staging.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "contracts" / "public-openapi-v0.1.json"
    )
    args = parser.parse_args()
    export_openapi(args.output)
    print(f"Exported public business OpenAPI (contract {CONTRACT_VERSION})")


if __name__ == "__main__":
    main()
