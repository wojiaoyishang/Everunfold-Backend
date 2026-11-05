"""Yjs 13 / Yrs wire interop; projections contain no access policy or account data."""

import json
import re
from typing import Any
from uuid import UUID

from pycrdt import Array, Decoder, Doc, Map, Text, get_update

from app.modules.content.collaboration.position_history import position_history
from app.modules.content.collaboration.rich_text import initialize_block, projected_html
from app.modules.content.draft_schemas import DraftFields
from app.modules.content.editor_schema import EditorSettings

TEXT_KEYS = ("title", "excerpt", "seo_title", "seo_description")
POLICY_KEYS = {"visibility", "opens_at"}
SETTING_KEYS = set(EditorSettings.model_fields) - POLICY_KEYS - set(TEXT_KEYS)
MAX_UPDATE = 1_048_576
MAX_STATE = 4_194_304


def seed(fields: DraftFields, *, source_first: bool = False) -> Doc[Any]:
    settings = (fields.settings or EditorSettings()).model_dump(mode="json")
    doc: Doc[Any] = Doc(
        {
            "meta": Map(
                {
                    "title": Text(fields.title),
                    "excerpt": Text(fields.excerpt),
                    "seo_title": Text(str(settings.pop("seo_title"))),
                    "seo_description": Text(str(settings.pop("seo_description"))),
                    "settings": Map({k: v for k, v in settings.items() if k in SETTING_KEYS}),
                }
            ),
            "order": Array([str(block.id) for block in fields.body]),
            "blocks": Map(
                {
                    str(block.id): Map(
                        {"type": block.type, "content": Text(block.content), "deleted": False}
                    )
                    for block in fields.body
                }
            ),
        }
    )
    if not source_first:
        prepare_rich(doc)
    return doc


def prepare_rich(doc: Doc[Any]) -> bool:
    changed = False
    for block in doc.get("blocks", type=Map).values():
        if isinstance(block, Map) and block.get("type") == "html" and "rich" not in block:
            initialize_block(block)
            changed = True
    return changed


def decode(state: bytes) -> Doc[Any]:
    doc: Doc[Any] = Doc()
    doc.apply_update(state)
    return doc


def project(doc: Doc[Any], policy: EditorSettings | None = None) -> DraftFields:
    position_history(doc)
    meta = doc.get("meta", type=Map)
    blocks = doc.get("blocks", type=Map)
    order = doc.get("order", type=Array)
    if set(meta.keys()) != {*TEXT_KEYS, "settings"} or not isinstance(meta["settings"], Map):
        raise ValueError("未知的协同字段")
    if any(not isinstance(meta[key], Text) for key in TEXT_KEYS):
        raise ValueError("共享文本格式错误")
    settings = meta["settings"].to_py()
    assert settings is not None
    if set(settings) - SETTING_KEYS:
        raise ValueError("访问策略不能通过协同文档修改")
    settings.update({key: str(meta[key]) for key in TEXT_KEYS if key.startswith("seo_")})
    settings.update(
        {key: (policy or EditorSettings()).model_dump(mode="json")[key] for key in POLICY_KEYS}
    )
    if len(blocks) > 512 or len(order) > 512:
        raise ValueError("正文块过多，请导出后整理")
    body: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, block in blocks.items():
        UUID(key)
        if (
            not isinstance(block, Map)
            or set(block.keys())
            - {"type", "content", "deleted", "rich", "rich_base", "rich_generation"}
            or not {"type", "content", "deleted"}.issubset(block.keys())
            or not isinstance(block["content"], Text)
            or not isinstance(block["deleted"], bool)
            or block["type"] not in {"markdown", "html"}
        ):
            raise ValueError("正文块格式错误")
        if len(projected_html(block)) > 100_000:
            raise ValueError("单块正文过长")
    for key in order:
        if not isinstance(key, str) or key not in blocks:
            raise ValueError("正文块顺序错误")
        block = blocks[key]
        if key in seen or block["deleted"]:
            continue
        seen.add(key)
        body.append({"id": key, "type": block["type"], "content": projected_html(block)})
    return DraftFields.model_validate(
        {
            "title": str(meta["title"]),
            "excerpt": str(meta["excerpt"]),
            "body": body,
            "settings": settings,
        }
    )


def require_integrated(update: bytes, doc: Doc[Any]) -> None:
    """Yrs does not re-encode unresolved dependencies: never ACK such an update."""
    vector = Decoder(doc.get_state())
    clocks = {vector.read_var_uint(): vector.read_var_uint() for _ in range(vector.read_var_uint())}
    remainder = Decoder(get_update(update, doc.get_state()))
    if remainder.read_var_uint() != 0:
        raise ValueError("协同更新缺少前置更新")
    for _ in range(remainder.read_var_uint()):
        client = remainder.read_var_uint()
        for _ in range(remainder.read_var_uint()):
            start, length = remainder.read_var_uint(), remainder.read_var_uint()
            if start + length > clocks.get(client, 0):
                raise ValueError("删除操作缺少前置更新")


def references(fields: DraftFields) -> set[str]:
    # Retain references even after a block is removed: local undo/offline updates may use it again.
    return set(
        re.findall(
            r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}",
            json.dumps(fields.model_dump(mode="json")),
        )
    )
