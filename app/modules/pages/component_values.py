"""Separate private editor values from the authored page delivered to readers."""

from pydantic import ValidationError

from app.modules.auth.service import AuthFailure
from app.modules.content.component_document import ComponentDocument
from app.modules.pages.schemas import PageCode, PageValue

SOURCE_FIELDS = ("html", "css", "javascript", "javascript_enabled", "javascript_position")


def check_component_sources(value: PageCode, previous: PageCode | None) -> None:
    if value.edit_mode != "components":
        return
    defaults = PageCode()
    for name in SOURCE_FIELDS:
        supplied = getattr(value, name)
        retained = getattr(previous, name) if previous is not None else getattr(defaults, name)
        if supplied == getattr(defaults, name):
            setattr(value, name, retained)
        elif supplied != retained:
            raise AuthFailure(422, 42296, "组件模式下请在组件中编辑 HTML、CSS 或脚本")


def page_document(value: PageCode) -> ComponentDocument:
    return value.component_document or ComponentDocument(version=1, nodes=[])


def public_page_value(value: PageValue, *, linked: bool = False) -> PageValue:
    clean = value.model_copy(update={"component_parameters": {}})
    if value.edit_mode == "source":
        return clean.model_copy(
            update={"component_document": None, "component_css": "", "component_css_override": ""}
        )
    document = page_document(value).public_document(value.component_parameters if linked else None)
    try:
        ComponentDocument.model_validate(document.model_dump())
    except ValidationError as exc:
        raise AuthFailure(422, 42296, "应用参数后的组件文档过大或格式不正确") from exc
    return clean.model_copy(
        update={
            "html": "",
            "css": "",
            "javascript": "",
            "javascript_enabled": False,
            "javascript_position": "body_end",
            "component_document": document,
        }
    )
