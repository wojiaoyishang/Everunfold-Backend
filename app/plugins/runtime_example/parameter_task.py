"""A bounded, local-only parameter example for the Worker invocation contract."""

import asyncio
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.plugin_sdk import task_progress


class CalculationOptions(BaseModel):
    weights: list[int] = Field(default_factory=lambda: [1, 2, 3], max_length=20)
    labels: list[str] = Field(default_factory=list, max_length=20)


class ExampleParameters(BaseModel):
    count: int = Field(ge=1, le=20, title="处理次数")
    enabled: bool = Field(default=True, title="执行计算")
    mode: Literal["sum", "product"] = Field(default="sum", title="计算方式")
    options: CalculationOptions = Field(default_factory=CalculationOptions, title="附加选项")


async def calculate(config: Settings, parameters: ExampleParameters) -> dict[str, int | bool | str]:
    for index in range(parameters.count):
        await task_progress(
            stage="calculate",
            message="Processing local parameter example",
            completed=index + 1,
            total=parameters.count,
            unit="items",
        )
        await asyncio.sleep(0)
    value = 0 if parameters.mode == "sum" else 1
    if parameters.enabled:
        for weight in parameters.options.weights:
            value = value + weight if parameters.mode == "sum" else value * weight
    return {
        "completed": parameters.count,
        "enabled": parameters.enabled,
        "mode": parameters.mode,
        "value": value,
        "label_count": len(parameters.options.labels),
    }
