"""Real Yrs XML projections, including marks, custom source and Unicode."""

from uuid import uuid4

import pytest
from pycrdt import Map, XmlText

from app.modules.content.collaboration.document import decode, project, seed
from app.modules.content.collaboration.rich_text import render_node, source_document, xml_document
from app.modules.content.draft_schemas import DraftBlock, DraftFields


@pytest.mark.parametrize(
    "source",
    [
        "<h2>标题😀</h2><p>一 <strong>二</strong> 三</p>",
        "<table><tbody><tr><td><p>单元格</p></td></tr></tbody></table>",
        '<div class="custom-widget" data-any="value">保留原始结构</div>',
        '<figure style="text-align:center"><img src="https://example.com/a.png" '
        'alt="图片" width="400"><figcaption>图片说明</figcaption></figure>',
        '<p><span data-font="site://media/11111111-1111-1111-1111-111111111111" '
        'style="font-family:ef-font-11111111-1111-1111-1111-111111111111;'
        'font-size:18px">字体</span></p>',
    ],
)
def test_initial_visual_projection_keeps_original_source(source: str) -> None:
    block_id = uuid4()
    doc = seed(DraftFields(body=[DraftBlock(id=block_id, type="html", content=source)]))
    assert project(decode(doc.get_update())).body[0].content == source
    block = doc.get("blocks", type=Map)[str(block_id)]
    canonical = render_node(xml_document(block["rich"]))
    assert "h2.0" not in canonical
    assert (
        "二" in canonical
        or "字体" in canonical
        or "单元格" in canonical
        or "custom-widget" in canonical
        or "图片说明" in canonical
    )


def test_rich_edit_is_projected_and_unknown_html_remains_raw() -> None:
    block_id = uuid4()
    doc = seed(DraftFields(body=[DraftBlock(id=block_id, type="html", content="<p>初始</p>")]))
    paragraph = doc.get("blocks", type=Map)[str(block_id)]["rich"].children[0]
    paragraph.children.append(XmlText("追加😀"))
    assert project(decode(doc.get_update())).body[0].content == "<p>初始追加😀</p>"
    source = '<div onclick="custom()"><x-private>保留</x-private></div>'
    assert render_node(source_document(source)) == source


def test_unknown_rich_nodes_are_rejected() -> None:
    with pytest.raises(ValueError):
        render_node({"type": "script", "content": []})
