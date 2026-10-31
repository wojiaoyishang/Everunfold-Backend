"""Shared strict-field DTO and HTTP envelope shapes for business modules."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicResponse[T](PublicModel):
    success: bool = True
    code: int = 200
    msg: str = "请求成功"
    data: T


class ErrorResponse(PublicModel):
    success: Literal[False]
    code: int
    msg: str
    data: Any = None
