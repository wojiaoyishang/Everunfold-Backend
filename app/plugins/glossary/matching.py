"""Bounded glossary rule evaluation over a filtered, article-scoped text projection."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import import_module
from typing import Any

MAX_PATTERN_LENGTH = 160
MAX_RULE_MATCHES = 40
MAX_TOTAL_MATCHES = 200


def validate_regex(pattern: str) -> None:
    """Reject constructs which can trigger pathological Python-regex backtracking.

    This deliberately supports a small, bounded subset: literals, character classes,
    groups, alternatives, anchors, and finite repetitions. Unbounded or nested
    repetitions, assertions, references and inline flags are not accepted.
    """
    _parser = import_module("re._parser")  # Pinned Python 3.12 grammar.

    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError("正则不能超过 160 个字符")
    if "(?" in pattern and re.search(r"\(\?[aiLmsux-]", pattern):
        raise ValueError("正则选项请使用独立的区分大小写开关")
    try:
        parsed = _parser.parse(pattern)
    except re.error as error:
        raise ValueError(f"正则语法错误：{error}") from error
    repetitions = 0
    branches = 0

    def check(items: Iterable[tuple[Any, Any]], *, repeated: bool = False, depth: int = 0) -> None:
        nonlocal repetitions, branches
        if depth > 8:
            raise ValueError("正则嵌套层级不能超过 8")
        for opcode, value in items:
            if opcode in {_parser.LITERAL, _parser.NOT_LITERAL, _parser.ANY, _parser.AT}:
                continue
            if opcode == _parser.IN:
                valid_entries = {_parser.LITERAL, _parser.RANGE, _parser.NEGATE, _parser.CATEGORY}
                if any(entry[0] not in valid_entries for entry in value):
                    raise ValueError("正则字符组包含不支持的写法")
            elif opcode == _parser.CATEGORY:
                continue
            elif opcode == _parser.SUBPATTERN:
                check(value[-1], repeated=repeated, depth=depth + 1)
            elif opcode == _parser.BRANCH:
                branches += 1
                if branches > 6 or len(value[1]) > 6:
                    raise ValueError("正则最多只能有 6 处分支，每处不超过 6 个选项")
                for branch in value[1]:
                    check(branch, repeated=repeated, depth=depth + 1)
            elif opcode in {_parser.MAX_REPEAT, _parser.MIN_REPEAT}:
                repetitions += 1
                minimum, maximum, body = value
                if repetitions > 6 or repeated or maximum == _parser.MAXREPEAT or maximum > 32:
                    raise ValueError("正则只能使用最多 6 个非嵌套的有限重复（最多 {0,32}）")
                if maximum > 1 and any(
                    token[0] in {_parser.SUBPATTERN, _parser.BRANCH} for token in body
                ):
                    raise ValueError("重复的分组请改写为固定长度的表达式")
                check(body, repeated=True, depth=depth + 1)
            else:
                raise ValueError("正则包含不支持的断言、反向引用或扩展语法")

    check(parsed)
    compiled = re.compile(pattern)
    if any(
        match.start() == match.end()
        for sample in ("", "abc 中 123", "AI 技术")
        for match in compiled.finditer(sample)
    ):
        raise ValueError("正则必须匹配至少一个可见字符，不能匹配空位置")


@dataclass(frozen=True)
class MatchRule:
    kind: str
    pattern: str
    enabled: bool = True
    case_sensitive: bool = False
    whole_word: bool = True
    priority: int = 0


@dataclass(frozen=True)
class TextHit:
    text: str
    case_sensitive: bool
    whole_word: bool
    priority: int


def evaluate_rules(source: str, rules: list[MatchRule]) -> list[TextHit]:
    """Return actual matched substrings, not regex source, within strict work budgets."""
    found: dict[tuple[str, bool, bool], TextHit] = {}
    for rule in rules:
        if not rule.enabled or not rule.pattern:
            continue
        if rule.kind == "regex":
            validate_regex(rule.pattern)
        expression = rule.pattern if rule.kind == "regex" else re.escape(rule.pattern)
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        regex = re.compile(expression, flags)
        for index, match in enumerate(regex.finditer(source)):
            if index >= MAX_RULE_MATCHES or len(found) >= MAX_TOTAL_MATCHES:
                break
            start, end = match.span()
            if start == end:
                continue
            if rule.whole_word and (
                (start > 0 and _ascii_word(source[start - 1]) and _ascii_word(source[start]))
                or (end < len(source) and _ascii_word(source[end - 1]) and _ascii_word(source[end]))
            ):
                continue
            hit = TextHit(match.group(), rule.case_sensitive, rule.whole_word, rule.priority)
            key = (
                hit.text if hit.case_sensitive else hit.text.casefold(),
                hit.case_sensitive,
                hit.whole_word,
            )
            previous = found.get(key)
            if previous is None or hit.priority > previous.priority:
                found[key] = hit
    return sorted(found.values(), key=lambda hit: (-hit.priority, -len(hit.text), hit.text))


def _ascii_word(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")
