"""Conservative source-preserving three-way merge, with stable block identity."""

from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from app.modules.content.version_schemas import MergeConflict


def merge_text(base: str, local: str, remote: str) -> str | None:
    if local == remote or remote == base:
        return local
    if local == base:
        return remote
    # Lines preserve markup byte-for-byte; overlapping edits remain explicit conflicts.
    lines = base.splitlines(keepends=True)
    if max(len(lines), len(local.splitlines()), len(remote.splitlines())) > 10_000:
        return None
    changes: list[tuple[int, int, list[str]]] = []
    for source in (local, remote):
        tokens = source.splitlines(keepends=True)
        for kind, start, end, other_start, other_end in SequenceMatcher(
            None, lines, tokens, autojunk=True
        ).get_opcodes():
            if kind == "equal":
                continue
            candidate = (start, end, tokens[other_start:other_end])
            if candidate in changes:
                continue
            for left, right, _ in changes:
                if (
                    max(left, start) < min(right, end)
                    or (start == end and left <= start <= right)
                    or (left == right and start <= left <= end)
                ):
                    return None
            changes.append(candidate)
    for start, end, replacement in sorted(changes, reverse=True):
        lines[start:end] = replacement
    return "".join(lines)


def three_way(
    base: dict[str, Any], local: dict[str, Any], remote: dict[str, Any]
) -> tuple[dict[str, Any], list[MergeConflict]]:
    conflicts: list[MergeConflict] = []

    def value(b: Any, ours: Any, r: Any, path: list[str], kind: str = "field") -> Any:
        if ours == r or r == b:
            return deepcopy(ours)
        if ours == b:
            return deepcopy(r)
        if isinstance(b, str) and isinstance(ours, str) and isinstance(r, str):
            text = merge_text(b, ours, r)
            if text is not None:
                return text
            kind = "text"
        conflicts.append(
            MergeConflict.model_validate(
                {
                    "key": "/".join(path),
                    "path": path,
                    "kind": kind,
                    "base": b,
                    "local": ours,
                    "remote": r,
                }
            )
        )
        return deepcopy(r)

    result = deepcopy(remote)
    for key in ("title", "excerpt"):
        result[key] = value(base.get(key, ""), local.get(key, ""), remote.get(key, ""), [key])
    result["settings"] = dict(remote.get("settings") or {})
    for key in (
        set(base.get("settings") or {})
        | set(local.get("settings") or {})
        | set(remote.get("settings") or {})
    ):
        if key in {"visibility", "opens_at"}:
            continue
        result["settings"][key] = value(
            (base.get("settings") or {}).get(key),
            (local.get("settings") or {}).get(key),
            (remote.get("settings") or {}).get(key),
            ["settings", key],
        )
    maps = [
        {str(block["id"]): block for block in fields["body"]} for fields in (base, local, remote)
    ]
    merged: dict[str, Any] = {}
    for block_id in sorted(set(maps[0]) | set(maps[1]) | set(maps[2])):
        b, ours, r = (mapping.get(block_id) for mapping in maps)
        if b and ours and r and b["type"] == ours["type"] == r["type"]:
            merged[block_id] = {
                **r,
                "content": value(
                    b["content"], ours["content"], r["content"], ["body", block_id, "content"]
                ),
            }
        else:
            merged[block_id] = value(b, ours, r, ["body", block_id], "block")
    common = set(maps[0]) & set(maps[1]) & set(maps[2])
    orders = [[str(b["id"]) for b in f["body"]] for f in (base, local, remote)]
    relative = [[key for key in order if key in common] for order in orders]
    if relative[1] != relative[0] and relative[2] != relative[0] and relative[1] != relative[2]:
        order = value(orders[0], orders[1], orders[2], ["order"], "order")
    else:
        order = list(orders[1] if relative[1] != relative[0] else orders[2])
    for alternate in (orders[2], orders[1], orders[0]):
        for index, key in enumerate(alternate):
            if key not in order:
                following = next((k for k in alternate[index + 1 :] if k in order), None)
                order.insert(order.index(following) if following else len(order), key)
    result["body"] = [merged[key] for key in order if merged.get(key) is not None]
    if not result["body"]:
        conflicts.append(
            MergeConflict(
                key="body",
                path=["body"],
                kind="field",
                base=base["body"],
                local=local["body"],
                remote=remote["body"],
            )
        )
        result["body"] = deepcopy(remote["body"])
    return result, conflicts


def resolve_value(result: dict[str, Any], path: list[str], value: Any) -> None:
    if path == ["body"]:
        result["body"] = value
    elif path == ["order"]:
        if not isinstance(value, list) or not all(isinstance(key, str) for key in value):
            raise ValueError("区块顺序格式无效")
        blocks = {str(block["id"]): block for block in result["body"]}
        order = list(dict.fromkeys([*value, *blocks]))
        result["body"] = [blocks[key] for key in order if key in blocks]
    elif path[0] == "body":
        index = next((i for i, b in enumerate(result["body"]) if str(b["id"]) == path[1]), None)
        if len(path) == 3:
            if index is None:
                raise ValueError("区块已移除")
            result["body"][index][path[2]] = value
        elif value is None:
            result["body"] = [b for b in result["body"] if str(b["id"]) != path[1]]
        elif index is None:
            result["body"].append(value)
        else:
            result["body"][index] = value
    elif len(path) == 2 and path[0] == "settings":
        result["settings"][path[1]] = value
    else:
        result[path[0]] = value
