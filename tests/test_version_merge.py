"""Three-way decisions preserve source and expose overlapping/structural edits."""

from uuid import uuid4

from app.modules.content.merge_algorithm import merge_text, resolve_value, three_way


def test_non_overlapping_lines_and_identical_insertions() -> None:
    assert merge_text("a\nb\nc\n", "A\nb\nc\n", "a\nb\nC\n") == "A\nb\nC\n"
    assert merge_text("a\n", "a\nx\n", "a\nx\n") == "a\nx\n"
    assert merge_text("a\n", "a\nx\n", "a\ny\n") is None
    assert merge_text("<p>A</p>\n", "<p>B</p>\n", "<p>C</p>\n") is None


def test_deletion_versus_edit_and_reorder_conflicts() -> None:
    ids = [str(uuid4()) for _ in range(3)]
    body = [{"id": key, "type": "html", "content": "<p>  原文 &amp; </p>\n"} for key in ids]
    base = {"title": "标题", "excerpt": "", "settings": {}, "body": body}
    local = {**base, "body": [body[1], body[2]]}
    remote = {**base, "body": [{**body[0], "content": "<p>线上</p>"}, *body[1:]]}
    result, conflicts = three_way(base, local, remote)
    assert len(conflicts) == 1 and conflicts[0].kind == "block"
    assert conflicts[0].local is None
    resolve_value(result, conflicts[0].path, conflicts[0].local)
    assert [block["id"] for block in result["body"]] == ids[1:]
    assert result["body"][0]["content"] == body[1]["content"]
    local = {**base, "body": [body[1], body[0], body[2]]}
    remote = {**base, "body": [body[0], body[2], body[1]]}
    _, conflicts = three_way(base, local, remote)
    assert [item.kind for item in conflicts] == ["order"]


def test_independent_block_insertions_are_retained() -> None:
    one, two, three = [str(uuid4()) for _ in range(3)]

    def block(key: str) -> dict[str, str]:
        return {"id": key, "type": "markdown", "content": key}

    base = {"title": "", "body": [block(one)], "settings": {}}
    result, conflicts = three_way(
        base,
        {**base, "body": [block(one), block(two)]},
        {**base, "body": [block(three), block(one)]},
    )
    assert not conflicts
    assert {b["id"] for b in result["body"]} == {one, two, three}
