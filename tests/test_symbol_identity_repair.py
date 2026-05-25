from dataclasses import replace
from pathlib import Path

from src.core.born_digital_math import (
    BornDigitalPage,
    PdfGlyph,
    PdfLine,
    PdfRegion,
    PdfSpan,
)
from src.core.pdf_glyph_graph import RawGlyphGraphExtractor
from src.core.symbol_identity_repair import (
    ENRICHED_GLYPH_GRAPH_SCHEMA_VERSION,
    SYMBOL_IDENTITY_REPAIR_VERSION,
    SymbolIdentityRepairer,
)


def test_symbol_identity_repair_preserves_known_pdf_text() -> None:
    graph = _graph([
        PdfGlyph("∑", "CMSY10", 10, (10, 10, 20, 20), cid=80),
    ])

    enriched = SymbolIdentityRepairer().repair_graph(graph)

    assert enriched.schema_version == ENRICHED_GLYPH_GRAPH_SCHEMA_VERSION
    assert enriched.repair_version == SYMBOL_IDENTITY_REPAIR_VERSION
    node = enriched.glyphs[0]
    assert node.resolved_identity is not None
    assert node.resolved_identity.unicode == "∑"
    assert node.resolved_identity.latex == r"\sum"
    assert node.resolved_identity.source == "pdf_text"
    assert node.warnings == ()
    assert enriched.summary.unknown_before == 0
    assert enriched.summary.unknown_after == 0


def test_symbol_identity_repair_uses_static_glyph_name_map() -> None:
    graph = _graph([
        PdfGlyph("cid:80", "CMSY10", 10, (10, 10, 20, 20), cid=80, glyph_name="summation"),
    ])

    enriched = SymbolIdentityRepairer().repair_graph(graph)

    node = enriched.glyphs[0]
    assert node.raw.is_unknown is True
    assert node.resolved_identity is not None
    assert node.resolved_identity.unicode == "∑"
    assert node.resolved_identity.latex == r"\sum"
    assert node.resolved_identity.source == "static_glyph_name_map"
    assert "glyph_name_lookup" in node.repair_trace
    assert enriched.summary.repaired_count == 1
    assert enriched.summary.unknown_after == 0


def test_symbol_identity_repair_propagates_same_font_cid_anchor() -> None:
    graph = _graph([
        PdfGlyph("∫", "ABCDEE+CMSY10", 10, (10, 10, 20, 20), cid=82),
        PdfGlyph("cid:82", "CMSY10", 10, (24, 10, 34, 20), cid=82),
    ])

    enriched = SymbolIdentityRepairer().repair_graph(graph)

    repaired = enriched.glyphs[1]
    assert repaired.resolved_identity is not None
    assert repaired.resolved_identity.unicode == "∫"
    assert repaired.resolved_identity.latex == r"\int"
    assert repaired.resolved_identity.source == "same_font_cid_anchor"
    assert "same_font_cid_anchor" in repaired.repair_trace
    assert enriched.summary.repaired_count == 1


def test_symbol_identity_repair_keeps_unknown_when_font_cid_anchor_conflicts() -> None:
    graph = _graph([
        PdfGlyph("∑", "CMSY10", 10, (10, 10, 20, 20), cid=80),
        PdfGlyph("∫", "CMSY10", 10, (24, 10, 34, 20), cid=80),
        PdfGlyph("cid:80", "CMSY10", 10, (38, 10, 48, 20), cid=80),
    ])

    enriched = SymbolIdentityRepairer().repair_graph(graph)

    repaired = enriched.glyphs[2]
    assert repaired.resolved_identity is None
    assert repaired.identity_candidates == ()
    assert "font_cid_identity_conflict" in repaired.warnings
    assert "unrecovered_identity" in repaired.warnings
    assert enriched.summary.conflict_count == 1
    assert enriched.summary.unknown_after == 1


def test_symbol_identity_repair_detects_candidate_conflict() -> None:
    graph = _graph([
        PdfGlyph("∑", "CMSY10", 10, (10, 10, 20, 20), cid=80),
        PdfGlyph("cid:80", "CMSY10", 10, (24, 10, 34, 20), cid=80, glyph_name="integral"),
    ])

    enriched = SymbolIdentityRepairer().repair_graph(graph)

    repaired = enriched.glyphs[1]
    assert repaired.resolved_identity is None
    assert {candidate.unicode for candidate in repaired.identity_candidates} == {"∑", "∫"}
    assert "identity_candidate_conflict" in repaired.warnings
    assert enriched.summary.conflict_count == 1
    assert enriched.summary.unknown_after == 1


def test_symbol_identity_repair_hash_is_stable_and_raw_graph_is_optional_in_json() -> None:
    graph = _graph([
        PdfGlyph("cid:112", "CMMI10", 10, (10, 10, 20, 20), cid=112, glyph_name="alpha"),
    ])
    repairer = SymbolIdentityRepairer()

    first = repairer.repair_graph(graph)
    second = repairer.repair_graph(graph)

    assert first.input_hash == second.input_hash
    assert first.to_json()["input_hash"] == first.input_hash
    assert "raw_graph" not in first.to_json(include_raw_graph=False)
    assert first.summary.raw_input_hash == graph.input_hash


def test_symbol_identity_repair_has_no_visual_ocr_dependency() -> None:
    source = Path("src/core/symbol_identity_repair.py").read_text(encoding="utf-8").lower()

    assert "pix2text" not in source
    assert "paddle" not in source
    assert "mineru" not in source
    assert "ocr" not in source
    assert "fitz.open" not in source


def test_raw_glyph_graph_carries_optional_glyph_name() -> None:
    graph = _graph([
        PdfGlyph("cid:80", "CMSY10", 10, (10, 10, 20, 20), cid=80, glyph_name="summation"),
    ])

    assert graph.glyphs[0].glyph_name == "summation"
    renamed = replace(graph.glyphs[0], glyph_name="integral")
    assert renamed.glyph_name == "integral"


def _graph(glyphs: list[PdfGlyph]):
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
    page = BornDigitalPage(
        page_num=0,
        page_size=(200, 100),
        regions=(
            PdfRegion(
                page_num=0,
                kind="text",
                bbox=span.bbox,
                text=span.text,
                lines=(line,),
            ),
        ),
        warnings=(),
    )
    return RawGlyphGraphExtractor().from_page_facts(page)


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    if not boxes:
        return (0, 0, 0, 0)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
