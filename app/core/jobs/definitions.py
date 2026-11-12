"""Pure declarations: handlers are imported only by their execution process."""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


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
    parameters: type[BaseModel] | None = None
    default_parameters: dict[str, Any] = field(default_factory=dict)

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
        if self.kind == "resident" and (self.parameters is not None or self.default_parameters):
            raise ValueError("Resident tasks do not accept invocation parameters")
        if self.parameters is not None and (
            not isinstance(self.parameters, type) or not issubclass(self.parameters, BaseModel)
        ):
            raise ValueError("Task parameters must be a Pydantic model class")
        from app.core.jobs.parameter_metadata import reject_secret_models
        from app.core.jobs.parameters import normalized_parameters

        if self.parameters is not None:
            reject_secret_models(self.parameters)
        object.__setattr__(
            self, "default_parameters", normalized_parameters(self, self.default_parameters)
        )


BUILTINS = (
    TaskDefinition(
        "core.notifications",
        "通知投递",
        "app.modules.notifications.jobs:deliver",
        interval=3,
        timeout=180,
        enabled=True,
    ),
    TaskDefinition(
        "core.mail-test",
        "测试邮件发送",
        "app.modules.settings.mail_delivery:deliver_test",
        interval=3,
        timeout=150,
        enabled=True,
    ),
    TaskDefinition(
        "core.email-verification",
        "邮箱验证发送",
        "app.modules.auth.email_verification_delivery:deliver",
        interval=3,
        timeout=180,
        enabled=True,
    ),
    TaskDefinition(
        "core.registration",
        "注册验证邮件发送",
        "app.modules.auth.registration_delivery:deliver",
        interval=3,
        timeout=180,
        enabled=True,
    ),
    TaskDefinition(
        "core.password-recovery",
        "找回密码邮件发送",
        "app.modules.auth.recovery_delivery:deliver",
        interval=3,
        timeout=180,
        enabled=True,
    ),
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
