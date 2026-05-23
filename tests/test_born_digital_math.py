import fitz

from src.core.born_digital_math import (
    BornDigitalMathAuditor,
    BornDigitalPage,
    MuPDFBornDigitalExtractor,
    PdfGlyph,
    PdfLine,
    PdfRegion,
    PdfSpan,
)


def test_mupdf_extractor_records_glyphs_and_vectors() -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((36, 48), "x2 + y2", fontsize=12)
    page.draw_line((36, 64), (96, 64), color=(0, 0, 0), width=0.5)

    extracted = MuPDFBornDigitalExtractor().extract_page(page, 0)

    text_regions = [region for region in extracted.regions if region.kind == "text"]
    vector_regions = [region for region in extracted.regions if region.kind == "vector"]

    assert extracted.page_num == 0
    assert extracted.glyph_count >= 7
    assert extracted.vector_count == 1
    assert text_regions[0].text == "x2 + y2"
    assert vector_regions[0].metadata["stroked"] is True


def test_mupdf_extractor_keeps_span_font_and_character_boxes() -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((36, 48), "abc", fontsize=11, fontname="helv")

    extracted = MuPDFBornDigitalExtractor().extract_page(page, 0)
    line = next(region.lines[0] for region in extracted.regions if region.kind == "text")
    span = line.spans[0]

    assert span.font
    assert span.size == 11
    assert [glyph.text for glyph in span.glyphs] == ["a", "b", "c"]
    assert all(glyph.bbox[2] >= glyph.bbox[0] for glyph in span.glyphs)


def test_mupdf_extractor_flags_unknown_glyphs_with_custom_flags() -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((36, 48), "?", fontsize=12)

    base = MuPDFBornDigitalExtractor().extract_page(page, 0)
    assert base.unknown_glyph_count == 0

    fake_raw = {
        "blocks": [
            {
                "type": 0,
                "bbox": (0, 0, 10, 10),
                "lines": [
                    {
                        "bbox": (0, 0, 10, 10),
                        "wmode": 0,
                        "dir": (1, 0),
                        "spans": [
                            {
                                "font": "BrokenFont",
                                "size": 10,
                                "bbox": (0, 0, 10, 10),
                                "chars": [{"c": "\ufffd", "bbox": (0, 0, 5, 10)}],
                            }
                        ],
                    }
                ],
            }
        ]
    }

    class FakePage:
        rect = fitz.Rect(0, 0, 240, 180)

        def get_text(self, kind: str, flags: int) -> dict[str, object]:
            assert kind == "rawdict"
            assert flags == 123
            return fake_raw

    extracted = MuPDFBornDigitalExtractor(flags=123).extract_page(FakePage(), 3)  # type: ignore[arg-type]

    assert extracted.page_num == 3
    assert extracted.unknown_glyph_count == 1
    assert extracted.warnings == ("unknown_glyph",)


def test_math_auditor_reports_pdf_structure_evidence() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(240, 180),
        warnings=(),
        regions=(
            _text_region([PdfGlyph("∈", "CMSY10", 12, (36, 40, 44, 52))]),
            PdfRegion(page_num=0, kind="vector", bbox=(36, 58, 72, 60)),
        ),
    )
    regions = BornDigitalMathAuditor(vector_margin=8).evidence_regions(extracted)

    assert regions
    assert "math_symbol" in regions[0].evidence
    assert "near_vector" in regions[0].evidence
    assert regions[0].source == "pdf_structure_evidence"


def test_math_auditor_keeps_math_evidence_at_glyph_group_granularity() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(360, 180),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("T", "Helvetica", 12, (36, 40, 44, 52)),
                PdfGlyph(" ", "Helvetica", 12, (44, 40, 48, 52)),
                PdfGlyph("∈", "CMSY10", 12, (104, 40, 112, 52)),
                PdfGlyph(" ", "Helvetica", 12, (112, 40, 116, 52)),
                PdfGlyph("a", "Helvetica", 12, (124, 40, 132, 52)),
            ]),
        ),
    )
    regions = BornDigitalMathAuditor(vector_margin=2).evidence_regions(extracted)

    assert len(regions) == 1
    assert regions[0].text == "∈"
    assert regions[0].bbox[0] >= 104
    assert regions[0].bbox[2] <= 118


def test_math_auditor_does_not_promote_plain_script_size_without_math_evidence() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(360, 180),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("d", "Helvetica", 12, (104, 40, 111, 52)),
                PdfGlyph("k", "Helvetica", 8, (111, 44, 116, 52)),
            ]),
        ),
    )
    regions = BornDigitalMathAuditor(vector_margin=2).evidence_regions(extracted)

    assert regions == []


def test_born_digital_audit_aligns_evidence_with_latex_source(tmp_path) -> None:
    from tools import born_digital_math_audit as audit

    pdf = tmp_path / "paper.pdf"
    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((36, 48), "x", fontsize=12, fontname="helv")
    doc.save(pdf)
    doc.close()
    (latex_root / "main.tex").write_text(r"$x$", encoding="utf-8")

    report = audit.audit_pdf(pdf, start_page=0, max_pages=1, sample_limit=2, latex_root=latex_root)

    assert "latex_source_alignment" in report
    assert report["latex_source_alignment"]["available"] is True
    assert report["latex_source_alignment"]["tex_file_count"] == 1


def test_math_auditor_clusters_adjacent_structure_evidence() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(360, 180),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("Q", "CMMI10", 12, (100, 40, 108, 52)),
                PdfGlyph("K", "CMMI10", 12, (109, 40, 117, 52)),
            ]),
            _text_region([
                PdfGlyph("T", "CMMI7", 8, (118, 34, 124, 42)),
            ]),
            _text_region([
                PdfGlyph("d", "CMMI10", 12, (110, 58, 118, 70)),
                PdfGlyph("k", "CMMI7", 8, (119, 63, 124, 71)),
            ]),
            PdfRegion(page_num=0, kind="vector", bbox=(98, 54, 128, 55)),
        ),
    )

    clusters = BornDigitalMathAuditor(vector_margin=4).evidence_clusters(extracted)

    assert len(clusters) == 1
    assert clusters[0].region_count == 3
    assert clusters[0].vector_count == 1
    assert "QK" in clusters[0].text


def test_math_auditor_contextual_clusters_include_adjacent_roman_glyphs() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(360, 180),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("A", "CMR10", 12, (80, 40, 88, 52)),
                PdfGlyph("(", "CMR10", 12, (88, 40, 92, 52)),
                PdfGlyph("Q", "CMMI10", 12, (93, 40, 101, 52)),
                PdfGlyph(",", "CMR10", 12, (101, 40, 104, 52)),
                PdfGlyph("K", "CMMI10", 12, (106, 40, 114, 52)),
                PdfGlyph(")", "CMR10", 12, (114, 40, 118, 52)),
            ]),
        ),
    )

    clusters = BornDigitalMathAuditor(vector_margin=2).contextual_clusters(extracted)

    assert len(clusters) == 1
    assert clusters[0].source == "pdf_structure_context_cluster"
    assert clusters[0].text == "A(Q,K)"


def _text_region(glyphs: list[PdfGlyph]) -> PdfRegion:
    bbox = (
        min(glyph.bbox[0] for glyph in glyphs),
        min(glyph.bbox[1] for glyph in glyphs),
        max(glyph.bbox[2] for glyph in glyphs),
        max(glyph.bbox[3] for glyph in glyphs),
    ) if glyphs else (0, 0, 0, 0)
    span = PdfSpan(
        text="".join(glyph.text for glyph in glyphs),
        font=glyphs[0].font if glyphs else "",
        size=glyphs[0].size if glyphs else 0,
        bbox=bbox,
        glyphs=tuple(glyphs),
    )
    line = PdfLine(
        text=span.text,
        bbox=bbox,
        writing_mode=0,
        direction=(1, 0),
        spans=(span,),
    )
    return PdfRegion(page_num=0, kind="text", bbox=bbox, text=line.text, lines=(line,))
