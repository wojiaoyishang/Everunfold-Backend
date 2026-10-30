"""The agreed HTTP JSON envelope, without freezing a business error dictionary."""

from collections.abc import Mapping
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class _BaseJSONResponse(JSONResponse):
    default_success: bool = False
    default_status_code: int = 200
    default_msg: str = ""

    def __init__(
        self,
        data: Any = None,
        msg: str | None = None,
        code: int | None = None,
        *,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        http_status = self.default_status_code if status_code is None else status_code
        self.success = self.default_success
        self.code = http_status if code is None else code
        self.msg = self.default_msg if msg is None else msg
        self.data = data
        payload: dict[str, Any] = {
            "success": self.success,
            "code": self.code,
            "msg": self.msg,
        }
        if data is not None:
            payload["data"] = jsonable_encoder(data)
        super().__init__(content=payload, status_code=http_status, headers=headers)


class JSONResponseSuccess(_BaseJSONResponse):
    default_success = True
    default_status_code = 200
    default_msg = "请求成功"


class JSONResponseFail(_BaseJSONResponse):
    default_status_code = 400
    default_msg = "请求失败"


class JSONResponseError(_BaseJSONResponse):
    default_status_code = 500
    default_msg = "处理失败"
