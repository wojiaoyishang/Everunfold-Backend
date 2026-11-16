"""The versioned component tree shared by content, pages, and reusable templates."""

import json
from collections.abc import Callable, Iterator
from typing import Literal, Self

from pydantic import Field, JsonValue, model_validator

from app.core.schemas import PublicModel

ContentEditMode = Literal["blocks", "components"]
PageEditMode = Literal["source", "components"]
PageLayout = Literal["wide", "sidebar", "wide_transparent", "blank"]
IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$"
SOURCE_COMPONENTS = {"core.html", "core.markdown", "core.shortcode"}


class ComponentParameter(PublicModel):
    id: str = Field(pattern=IDENTIFIER)
    label: str = Field(min_length=1, max_length=120)
    keep_default: bool = True


class ComponentNode(PublicModel):
    id: str = Field(pattern=IDENTIFIER)
    type: str = Field(pattern=r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_.-]{0,63}$", max_length=100)
    props: dict[str, JsonValue] = Field(max_length=64)
    css: str = Field(default="", max_length=4096, exclude_if=lambda value: not value)
    children: list["ComponentNode"] = Field(
        default_factory=list, max_length=300, exclude_if=lambda v: not v
    )
    parameters: dict[str, ComponentParameter] = Field(
        default_factory=dict, max_length=32, exclude_if=lambda v: not v
    )

    def walk(self, depth: int) -> Iterator[tuple["ComponentNode", int]]:
        yield self, depth
        for child in self.children:
            yield from child.walk(depth + 1)


class ComponentDocument(PublicModel):
    version: Literal[1]
    nodes: list[ComponentNode] = Field(max_length=300)

    def walk(self) -> Iterator[tuple[ComponentNode, int]]:
        for node in self.nodes:
            yield from node.walk(1)

    @model_validator(mode="after")
    def bounded_tree(self) -> Self:
        ids: set[str] = set()
        parameters: set[str] = set()
        for node, depth in self.walk():
            if depth > 16 or len(ids) >= 300:
                raise ValueError("组件最多 300 个，嵌套最多 16 层")
            if node.id in ids:
                raise ValueError("组件 ID 不能重复")
            ids.add(node.id)
            for key, parameter in node.parameters.items():
                if not key or len(key) > 80 or parameter.id in parameters:
                    raise ValueError("组件参数名称无效或 ID 重复")
                parameters.add(parameter.id)
        encoded = json.dumps(
            self.model_dump(), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if len(encoded.encode()) > 256 * 1024:
            raise ValueError("组件文档不能超过 256 KiB")
        return self

    def transform_sources(
        self, transform: Callable[[ComponentNode, str], str]
    ) -> "ComponentDocument":
        def update(node: ComponentNode) -> ComponentNode:
            props = dict(node.props)
            source = props.get("source")
            if node.type in SOURCE_COMPONENTS and isinstance(source, str):
                props["source"] = transform(node, source)
            return node.model_copy(
                update={"props": props, "children": [update(child) for child in node.children]}
            )

        return self.model_copy(update={"nodes": [update(node) for node in self.nodes]})

    def public_document(self, values: dict[str, JsonValue] | None = None) -> "ComponentDocument":
        def project(nodes: list[ComponentNode]) -> list[ComponentNode]:
            result = []
            for node in nodes:
                if node.type == "core.comment":
                    continue
                props = dict(node.props)
                if values is not None:
                    for key, parameter in node.parameters.items():
                        prior = props.get(key)
                        incoming = values.get(parameter.id)
                        if parameter.id in values and (
                            prior is None or type(incoming) is type(prior)
                        ):
                            props[key] = incoming
                        elif not parameter.keep_default:
                            props.pop(key, None)
                result.append(
                    node.model_copy(
                        update={
                            "props": props,
                            "children": project(node.children),
                            "parameters": {},
                        }
                    )
                )
            return result

        return self.model_copy(update={"nodes": project(self.nodes)})

    def text(self) -> str:
        return "\n".join(
            value
            for node, _ in self.walk()
            if node.type != "core.comment"
            for key in ("source", "text", "title", "subtitle", "label")
            if isinstance(value := node.props.get(key), str)
        )
