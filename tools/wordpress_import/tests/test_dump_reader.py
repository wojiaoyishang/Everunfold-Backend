from pathlib import Path

import pytest

from tools.wordpress_import.links import PublicLinkResolver
from tools.wordpress_import.media import MediaResolver
from tools.wordpress_import.mysql_dump import (
    DumpFormatError,
    iter_table_rows,
    parse_values,
    php_array,
)
from tools.wordpress_import.text import plain_text, rewrite_html, rewrite_markdown


def test_mysql_values_preserve_escaped_delimiters_and_null() -> None:
    values = r"(1,'中,文 (正文);\n\'quote\' and \\ slash',NULL),(2,'it''s ok',-2.5);"
    assert list(parse_values(values)) == [
        [1, "中,文 (正文);\n'quote' and \\ slash", None],
        [2, "it's ok", -2.5],
    ]


def test_reader_ignores_binary_unlisted_tables_and_handles_multiline(tmp_path: Path) -> None:
    source = tmp_path / "backup.sql"
    source.write_bytes(
        b"INSERT INTO `private` VALUES (1,'\xffbinary');\n"
        b"CREATE TABLE `public` (\n  `id` int,\n  `body` text\n);\n"
        b"INSERT INTO `public` VALUES (1,'first;\nsecond,(third)');\n"
    )
    assert list(iter_table_rows(source, {"public"})) == [
        ("public", {"id": 1, "body": "first;\nsecond,(third)"})
    ]


@pytest.mark.parametrize("value", ["(1,NOW());", "(1,'unfinished)", "(1,NULL"])
def test_parser_refuses_expressions_and_truncated_values(value: str) -> None:
    with pytest.raises(DumpFormatError):
        list(parse_values(value))


def test_php_serialized_string_lengths_are_bytes() -> None:
    assert php_array('a:1:{s:6:"中文";s:3:"值";}') == {"中文": "值"}
    with pytest.raises(DumpFormatError):
        php_array('O:8:"stdClass":0:{}')


def test_html_and_markdown_rewrite_resource_nodes_but_not_code() -> None:
    def resolver(value: str) -> str:
        return "site://media/example" if value == "/image.jpg" else value

    source = '<img src="/image.jpg"><pre><code>&lt;img src="/image.jpg"&gt;</code></pre> /image.jpg'
    result = rewrite_html(source, resolver)
    assert '<img src="site://media/example">' in result
    assert '&lt;img src="/image.jpg"&gt;' in result
    assert result.endswith(" /image.jpg")
    markdown = '![图片](/image.jpg) `![代码](/image.jpg)`\n```html\n<img src="/image.jpg">\n```\n'
    converted = rewrite_markdown(markdown, resolver)
    assert "![图片](site://media/example)" in converted
    assert "`![代码](/image.jpg)`" in converted
    assert '<img src="/image.jpg">' in converted


def test_media_paths_reject_traversal_active_files_and_fake_images(tmp_path: Path) -> None:
    uploads = tmp_path / "wp-content" / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "fake.jpg").write_text("<script>active</script>")
    resolver = MediaResolver(tmp_path, "https://example.com", None, "/uploads", True)
    assert resolver.resolve("/wp-content/uploads/../../secret.jpg") == ""
    assert resolver.resolve("/wp-content/uploads/active.svg") == ""
    assert resolver.resolve("/wp-content/uploads/fake.jpg") == ""
    assert plain_text("<script>secret()</script><p>公开正文</p>") == "公开正文"


def test_longer_fences_and_exact_inline_runs_preserve_code() -> None:
    def resolve(value: str) -> str:
        return "site://media/example" if value == "/image.jpg" else value

    source = "````html\n```\n![代码](/image.jpg)\n````\n![正文](/image.jpg)\n"
    result = rewrite_markdown(source, resolve)
    assert "![代码](/image.jpg)" in result
    assert "![正文](site://media/example)" in result
    inline = "``code ` and ``` ![代码](/image.jpg)`` ![正文](/image.jpg)"
    result = rewrite_markdown(inline, resolve)
    assert "![代码](/image.jpg)" in result
    assert "![正文](site://media/example)" in result
    assert rewrite_markdown("unmatched ](/image.jpg)", resolve) == "unmatched ](/image.jpg)"


def test_same_origin_links_are_node_scoped_and_keep_query_and_fragment() -> None:
    mapper = PublicLinkResolver("https://example.com", {"/about/", "/"})

    def keep(value: str) -> str:
        return value

    html_source = '<a href="https://example.com/about?q=one#part">关于</a><code>https://example.com/about</code>'
    converted = rewrite_html(html_source, keep, mapper.resolve)
    assert 'href="/about/?q=one#part"' in converted
    assert "<code>https://example.com/about</code>" in converted
    markdown = "[关于](https://example.com/about#part) `[代码](https://example.com/about)`"
    converted = rewrite_markdown(markdown, keep, mapper.resolve)
    assert "[关于](/about/#part)" in converted
    assert "`[代码](https://example.com/about)`" in converted
    assert mapper.resolve("https://other.example/about") == "https://other.example/about"
    assert mapper.resolve("/independent-app") == "https://example.com/independent-app"
    assert "/independent-app" in mapper.unmapped
