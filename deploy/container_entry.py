"""Load root YAML environment before starting any backend child process."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from listener_address import listener_host  # noqa: E402

from app.core.config import configuration_path, load_settings  # noqa: E402
from app.core.deployment_config import read_deployment  # noqa: E402


def main() -> None:
    selected = configuration_path(project_root=ROOT)
    print(f"Everunfold Backend · CONFIG_FILE={selected}", flush=True)
    if not selected.is_file():
        raise ValueError(
            f"找不到项目配置 {selected}；请先在项目根目录运行 ./setup.sh --docker 并保存。"
        )
    environment = dict(os.environ)
    if selected.suffix.lower() in {".yaml", ".yml"}:
        document = read_deployment(selected)
        environment = {**document.get("environment", {}), **environment}
    if environment.get("EVERUNFOLD_LISTEN_CIDRS"):
        environment["API_HOST"] = listener_host(environment)
    load_settings(selected, environ=environment)
    environment["EVERUNFOLD_CONFIG_LOADED"] = "1"
    environment["CONFIG_FILE"] = str(selected)
    entry = "/usr/local/bin/everunfold-backend"
    os.execve(entry, [entry, *sys.argv[1:]], environment)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Everunfold 启动检查失败：{error}") from None
