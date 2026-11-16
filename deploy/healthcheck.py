"""Probe the API listener configured in root YAML, including direct docker run."""

import os
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import configuration_path  # noqa: E402
from app.core.deployment_config import read_deployment  # noqa: E402


def main() -> None:
    environment = dict(os.environ)
    path = configuration_path()
    if path.suffix.lower() in {".yaml", ".yml"}:
        environment = {**read_deployment(path).get("environment", {}), **environment}
    host = environment.get("API_HOST", "0.0.0.0")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    host = f"[{host}]" if ":" in host else host
    port = environment.get("API_PORT", "8001")
    with urlopen(f"http://{host}:{port}/health/ready", timeout=4):
        pass


if __name__ == "__main__":
    main()
