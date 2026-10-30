"""Read a mysqldump without executing SQL or treating data as Python code."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_CREATE = re.compile(r"^CREATE TABLE `([^`]+)`", re.MULTILINE)
_INSERT = re.compile(r"^INSERT INTO `([^`]+)`\s*(?:\((.*?)\))?\s*VALUES\s*", re.DOTALL)
_COLUMN = re.compile(r"^\s+`([^`]+)`\s+", re.MULTILINE)
_ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


class DumpFormatError(ValueError):
    """A malformed or unsupported SQL construct; errors never include source data."""


def _statement_complete(text: str) -> bool:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == ";":
            return True
        index += 1
    return False


def parse_values(text: str) -> Iterator[list[str | int | float | None]]:
    """Parse VALUES tuples with MySQL escapes, NULL and embedded delimiters.

    The parser deliberately accepts literal values only. It never evaluates SQL,
    functions, comments, hexadecimal expressions or Python expressions.
    """
    index = 0
    length = len(text)
    while index < length:
        while index < length and (text[index].isspace() or text[index] in ",;"):
            index += 1
        if index == length:
            return
        if text[index] != "(":
            raise DumpFormatError("Expected a VALUES tuple")
        index += 1
        row: list[str | int | float | None] = []
        while True:
            while index < length and text[index].isspace():
                index += 1
            if index >= length:
                raise DumpFormatError("Unterminated VALUES tuple")
            if text[index] in {"'", '"'}:
                quote = text[index]
                index += 1
                value: list[str] = []
                while index < length:
                    char = text[index]
                    index += 1
                    if char == "\\":
                        if index >= length:
                            raise DumpFormatError("Unterminated string escape")
                        escaped = text[index]
                        index += 1
                        value.append(
                            "\\" + escaped
                            if escaped in {"%", "_"}
                            else _ESCAPES.get(escaped, escaped)
                        )
                    elif char == quote:
                        if index < length and text[index] == quote:
                            value.append(quote)
                            index += 1
                        else:
                            break
                    else:
                        value.append(char)
                else:
                    raise DumpFormatError("Unterminated quoted string")
                row.append("".join(value))
            else:
                start = index
                while index < length and text[index] not in ",)":
                    index += 1
                literal = text[start:index].strip()
                if literal.upper() == "NULL":
                    row.append(None)
                else:
                    try:
                        row.append(
                            float(literal) if any(c in literal for c in ".eE") else int(literal)
                        )
                    except ValueError as exc:
                        raise DumpFormatError("Unsupported non-literal SQL value") from exc
            while index < length and text[index].isspace():
                index += 1
            if index >= length:
                raise DumpFormatError("Unterminated tuple after value")
            delimiter = text[index]
            index += 1
            if delimiter == ")":
                yield row
                break
            if delimiter != ",":
                raise DumpFormatError("Expected tuple delimiter")


def iter_table_rows(path: Path, tables: set[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield rows only for the explicitly allowed full table names."""
    columns: dict[str, list[str]] = {}
    current_create: str | None = None
    create_lines: list[str] = []
    insert_lines: list[str] = []
    # Unrelated plugin tables may contain binary bytes. Preserve them while
    # scanning instead of corrupting text or failing before the allowed tables.
    with path.open("r", encoding="utf-8-sig", errors="surrogateescape", newline="") as source:
        for line in source:
            if current_create:
                create_lines.append(line)
                if line.startswith(")"):
                    columns[current_create] = _COLUMN.findall("".join(create_lines))
                    current_create = None
                    create_lines = []
                continue
            if insert_lines:
                insert_lines.append(line)
                if not _statement_complete("".join(insert_lines)):
                    continue
                statement = "".join(insert_lines)
                insert_lines = []
            else:
                create_match = _CREATE.match(line)
                if create_match and create_match.group(1) in tables:
                    current_create = create_match.group(1)
                    create_lines = [line]
                    continue
                if not line.startswith("INSERT INTO `"):
                    continue
                table_end = line.find("`", len("INSERT INTO `"))
                table = line[len("INSERT INTO `") : table_end]
                if table not in tables:
                    continue
                if not _statement_complete(line):
                    insert_lines = [line]
                    continue
                statement = line
            match = _INSERT.match(statement)
            if match is None:
                raise DumpFormatError("Unsupported INSERT header")
            table = match.group(1)
            row_columns = (
                re.findall(r"`([^`]+)`", match.group(2))
                if match.group(2)
                else columns.get(table, [])
            )
            if not row_columns:
                raise DumpFormatError("INSERT references a table without column definitions")
            for values in parse_values(statement[match.end() :]):
                if len(values) != len(row_columns):
                    raise DumpFormatError(f"Column count mismatch in table {table}")
                yield table, dict(zip(row_columns, values, strict=True))
    if insert_lines or current_create:
        raise DumpFormatError("Truncated dump statement")


def php_array(value: str) -> Any:
    """Decode primitive PHP serialized values; never instantiate PHP objects."""
    data = value.encode("utf-8")
    position = 0

    def take(delimiter: bytes) -> bytes:
        nonlocal position
        end = data.find(delimiter, position)
        if end < 0:
            raise DumpFormatError("Malformed serialized option")
        result = data[position:end]
        position = end + len(delimiter)
        return result

    def parse() -> Any:
        nonlocal position
        if position >= len(data):
            raise DumpFormatError("Truncated serialized option")
        kind = chr(data[position])
        position += 1
        if kind == "N":
            if data[position : position + 1] != b";":
                raise DumpFormatError("Malformed serialized null")
            position += 1
            return None
        if data[position : position + 1] != b":":
            raise DumpFormatError("Malformed serialized delimiter")
        position += 1
        if kind in {"i", "b", "d"}:
            literal = take(b";")
            return (
                bool(int(literal))
                if kind == "b"
                else float(literal)
                if kind == "d"
                else int(literal)
            )
        if kind == "s":
            size = int(take(b':"'))
            string_value = data[position : position + size].decode("utf-8")
            position += size
            if data[position : position + 2] != b'";':
                raise DumpFormatError("Malformed serialized string length")
            position += 2
            return string_value
        if kind == "a":
            count = int(take(b":{"))
            array_value: dict[Any, Any] = {}
            for _ in range(count):
                key = parse()
                array_value[key] = parse()
            if data[position : position + 1] != b"}":
                raise DumpFormatError("Malformed serialized array")
            position += 1
            return array_value
        raise DumpFormatError("Serialized objects and references are not supported")

    result = parse()
    if position != len(data):
        raise DumpFormatError("Unexpected serialized option suffix")
    return result
