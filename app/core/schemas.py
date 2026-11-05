"""Shared strict-field DTO and HTTP envelope shapes for business modules."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.i18n import i18n


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicResponse[T](PublicModel):
    success: bool = True
    code: int = 200
    msg: str = "请求成功"
    data: T

    @model_validator(mode="after")
    def localize_message(self) -> "PublicResponse[T]":
        self.msg = i18n.message(self.msg)
        return self


class ErrorResponse(PublicModel):
    success: Literal[False]
    code: int
    msg: str
    data: Any = None
