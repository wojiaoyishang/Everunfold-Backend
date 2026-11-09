"""Pure declarations: handlers are imported only by their execution process."""

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    title: str
    handler: str
    kind: Literal["periodic", "resident"] = "periodic"
    interval: int = 60
    timeout: int = 120
    manual: bool = False
    enabled: bool = False

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", self.id)
            or not re.fullmatch(r"app\.[a-zA-Z0-9_.]+:[a-zA-Z_][a-zA-Z0-9_]*", self.handler)
            or not self.title
            or self.kind not in {"periodic", "resident"}
            or not 1 <= self.interval <= 86400
            or not 5 <= self.timeout <= 3600
        ):
            raise ValueError("Invalid task definition")


BUILTINS = (
    TaskDefinition(
        "core.settings",
        "设置与内容同步",
        "app.core.jobs.handlers:settings",
        interval=1,
        enabled=True,
    ),
    TaskDefinition(
        "core.callbacks", "应用回调", "app.core.jobs.handlers:callbacks", interval=1, enabled=True
    ),
    TaskDefinition(
        "core.publication",
        "定时发布",
        "app.core.jobs.handlers:publication",
        interval=1,
        enabled=True,
    ),
)


def definitions() -> dict[str, tuple[str, TaskDefinition]]:
    from app.core.plugins.loader import plugin_catalog

    result = {item.id: ("core", item) for item in BUILTINS}
    for plugin in plugin_catalog().plugins:
        for item in plugin.tasks:
            result[item.id] = (plugin.info.id, item)
    return result
