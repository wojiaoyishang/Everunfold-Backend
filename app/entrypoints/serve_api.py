"""Create one disposable metrics directory before Uvicorn spawns its workers."""

import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.monitor_config import monitoring_config


def main() -> None:
    with TemporaryDirectory(prefix="everunfold-api-") as path:
        if monitoring_config():
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = path
        from uvicorn.config import LOGGING_CONFIG
        from uvicorn.main import main as uvicorn

        config = copy.deepcopy(LOGGING_CONFIG)
        for kind in ("default", "access"):
            config["formatters"][kind] = {
                "()": "app.core.process_logging.JsonLogFormatter",
                "component": "fastapi",
            }
        filename = Path(path) / "logging.json"
        filename.write_text(json.dumps(config), encoding="utf-8")
        uvicorn(
            standalone_mode=True, default_map={"log_config": str(filename), "proxy_headers": False}
        )


if __name__ == "__main__":
    main()
