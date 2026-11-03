"""Validate y-tiptap 3 position history without opening arbitrary document roots."""

import re
from typing import Any

from pycrdt import Doc, Map

BUSINESS_ROOTS = {"meta", "blocks", "order"}
ROOT = re.compile(r"__tiptap_position_migrations:item:[0-9]{1,16}:[0-9]{1,16}\Z")
MAX_SAFE_INTEGER = 2**53 - 1


def natural(value: Any, minimum: int = 0) -> bool:
    # Yrs JSON numbers round-trip as floats; reject fractions, booleans and strings.
    return (
        type(value) in {int, float} and minimum <= value <= MAX_SAFE_INTEGER and value == int(value)
    )


def identity(value: Any, extra: set[str] | None = None) -> bool:
    return (
        isinstance(value, dict)
        and {"client", "clock"}.issubset(value)
        and not set(value) - {"client", "clock"} - (extra or set())
        and natural(value["client"])
        and natural(value["clock"])
    )


def boundary(value: dict[str, Any]) -> bool:
    return "boundary" not in value or value["boundary"] in ("start", "end")


def record_valid(key: str, value: Any) -> bool:
    if (
        not identity(value, {"version", "seen", "transfers"})
        or set(value) != {"version", "client", "clock", "seen", "transfers"}
        or type(value["version"]) not in {int, float}
        or value["version"] != 1
        or key != f"{int(value['client'])}:{int(value['clock'])}"
    ):
        return False
    seen, transfers = value["seen"], value["transfers"]
    if not isinstance(seen, list) or len(seen) > 4096:
        return False
    if any(
        not isinstance(pair, list) or len(pair) != 2 or not all(natural(v) for v in pair)
        for pair in seen
    ):
        return False
    if not isinstance(transfers, list) or len(transfers) > 20_000:
        return False
    for transfer in transfers:
        if not isinstance(transfer, dict) or set(transfer) != {"source", "target"}:
            return False
        source, target = transfer["source"], transfer["target"]
        if (
            not identity(source, {"length", "boundary"})
            or not natural(source.get("length"), 1)
            or not boundary(source)
        ):
            return False
        if target is None:
            continue
        # Our XML fragments are nested block types; top-level tname targets do not apply.
        if (
            not identity(target, {"type", "assoc", "boundary"})
            or "type" not in target
            or (target["type"] is not None and not identity(target["type"]))
            or (
                "assoc" in target
                and (type(target["assoc"]) not in {int, float} or target["assoc"] not in (-1, 0, 1))
            )
            or not boundary(target)
        ):
            return False
    return True


def position_history(doc: Doc[Any]) -> dict[str, dict[str, Any]]:
    roots = set(doc.keys())
    extra = roots - BUSINESS_ROOTS
    if not BUSINESS_ROOTS.issubset(roots) or len(extra) > 1024:
        raise ValueError("unknown document roots")
    result = {}
    for name in extra:
        if not ROOT.fullmatch(name):
            raise ValueError("unknown document roots")
        value = doc.get(name, type=Map).to_py()
        if not isinstance(value, dict) or len(value) > 20_000:
            raise ValueError("position history size")
        if any(not record_valid(key, item) for key, item in value.items()):
            raise ValueError("invalid position history")
        result[name] = value
    return result


def require_append_only(before: dict[str, dict[str, Any]], doc: Doc[Any]) -> None:
    after = position_history(doc)
    if any(
        key not in after.get(name, {}) or after[name][key] != value
        for name, entries in before.items()
        for key, value in entries.items()
    ):
        raise ValueError("position history is append-only")
