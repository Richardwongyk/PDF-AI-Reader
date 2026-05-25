import fitz

from src.core.born_digital_math import (
    BornDigitalPage,
    PdfFontResource,
    PdfGlyph,
    PdfLine,
    PdfRegion,
    PdfSpan,
)
from src.core.pdf_glyph_graph import (
    RAW_GLYPH_GRAPH_SCHEMA_VERSION,
    RawGlyphGraphExtractor,
    normalize_pdf_font_name,
)


def test_raw_glyph_graph_records_pdf_facts_without_ocr() -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((36, 48), "x2 + y2", fontsize=12, fontname="helv")
    page.draw_line((36, 64), (96, 64), color=(0, 0, 0), width=0.5)

    graph = RawGlyphGraphExtractor().extract_page(page, 0)

    assert graph.schema_version == RAW_GLYPH_GRAPH_SCHEMA_VERSION
    assert graph.page_num == 0
    assert graph.health.glyph_count >= 7
    assert graph.health.vector_count == 1
    assert graph.health.image_count == 0
    assert graph.glyphs[0].text == "x"
    assert graph.glyphs[0].bbox[2] >= graph.glyphs[0].bbox[0]
    assert graph.vectors[0].kind == "vector"
    assert graph.vectors[0].metadata["stroked"] is True
    assert graph.input_hash


def test_raw_glyph_graph_records_image_only_pages() -> None:
    page_facts = BornDigitalPage(
        page_num=2,
        page_size=(200, 100),
        regions=(
            PdfRegion(
                page_num=2,
                kind="image",
                bbox=(10, 10, 80, 70),
                metadata={"width": 70, "height": 60, "ext": "png"},
            ),
        ),
        warnings=(),
    )

    graph = RawGlyphGraphExtractor().from_page_facts(page_facts)

    assert graph.glyphs == ()
    assert len(graph.images) == 1
    assert graph.health.image_only_page is True
    assert graph.health.needs_symbol_repair is False


def test_raw_glyph_graph_flags_unknown_glyphs_for_symbol_repair() -> None:
    page_facts = BornDigitalPage(
        page_num=0,
        page_size=(200, 100),
        warnings=("unknown_glyph",),
        regions=(
            _text_region([
                PdfGlyph("x", "CMMI10", 10, (10, 10, 20, 20)),
                PdfGlyph("\ufffd", "CMSY10", 10, (22, 10, 32, 20)),
                PdfGlyph("cid:42", "CMSY10", 10, (34, 10, 44, 20), cid=42),
            ]),
        ),
        fonts=(
            PdfFontResource(
                xref=12,
                extension="",
                font_type="Type1",
                name="ABCDEE+CMSY10",
                resource_name="F1",
                encoding="Custom",
                embedded=1,
            ),
        ),
    )

    graph = RawGlyphGraphExtractor().from_page_facts(page_facts)

    assert graph.health.glyph_count == 3
    assert graph.health.unknown_glyph_count == 2
    assert graph.health.needs_symbol_repair is True
    assert graph.fonts[0].normalized_name == "CMSY10"
    assert [glyph.is_unknown for glyph in graph.glyphs] == [False, True, True]
    assert graph.glyphs[2].cid == 42


def test_raw_glyph_graph_hash_is_stable_for_same_facts() -> None:
    page_facts = BornDigitalPage(
        page_num=0,
        page_size=(200, 100),
        warnings=(),
        regions=(_text_region([PdfGlyph("a", "Helvetica", 10, (10, 10, 20, 20))]),),
    )

    first = RawGlyphGraphExtractor().from_page_facts(page_facts)
    second = RawGlyphGraphExtractor().from_page_facts(page_facts)

    assert first.input_hash == second.input_hash
    assert first.to_json()["input_hash"] == first.input_hash
    assert first.to_json(include_input_hash=False).get("input_hash") is None


def test_raw_glyph_graph_edges_preserve_span_and_line_order() -> None:
    line = PdfLine(
        text="ab",
        bbox=(10, 10, 30, 20),
        writing_mode=0,
        direction=(1, 0),
        spans=(
            PdfSpan(
                text="a",
                font="Helvetica",
                size=10,
                bbox=(10, 10, 20, 20),
                glyphs=(PdfGlyph("a", "Helvetica", 10, (10, 10, 20, 20)),),
            ),
            PdfSpan(
                text="b",
                font="Helvetica",
                size=10,
                bbox=(20, 10, 30, 20),
                glyphs=(PdfGlyph("b", "Helvetica", 10, (20, 10, 30, 20)),),
            ),
        ),
    )
    page_facts = BornDigitalPage(
        page_num=0,
        page_size=(200, 100),
        warnings=(),
        regions=(PdfRegion(page_num=0, kind="text", bbox=(10, 10, 30, 20), text="ab", lines=(line,)),),
    )

    graph = RawGlyphGraphExtractor().from_page_facts(page_facts)

    assert [edge.kind for edge in graph.edges] == ["next_in_line"]
    assert graph.edges[0].source == graph.glyphs[0].node_id
    assert graph.edges[0].target == graph.glyphs[1].node_id


def test_normalize_pdf_font_name_only_strips_standard_subset_prefix() -> None:
    assert normalize_pdf_font_name("ABCDEE+CMMI10") == "CMMI10"
    assert normalize_pdf_font_name("ABC+CMMI10") == "ABC+CMMI10"
    assert normalize_pdf_font_name("abcdee+CMMI10") == "abcdee+CMMI10"
    assert normalize_pdf_font_name("Helvetica") == "Helvetica"


def _text_region(glyphs: list[PdfGlyph]) -> PdfRegion:
    span = PdfSpan(
        text="".join(glyph.text for glyph in glyphs),
        font=glyphs[0].font if glyphs else "",
        size=glyphs[0].size if glyphs else 0.0,
        bbox=_union([glyph.bbox for glyph in glyphs]),
        glyphs=tuple(glyphs),
    )
    line = PdfLine(
        text=span.text,
        bbox=span.bbox,
        writing_mode=0,
        direction=(1, 0),
        spans=(span,),
    )
    return PdfRegion(
        page_num=0,
        kind="text",
        bbox=span.bbox,
        text=span.text,
        lines=(line,),
    )


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    if not boxes:
        return (0, 0, 0, 0)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
