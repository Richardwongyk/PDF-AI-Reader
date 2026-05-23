import fitz

from src.core.born_digital_math import (
    BornDigitalMathAuditor,
    BornDigitalPage,
    FormulaSegmentationConfig,
    MuPDFBornDigitalExtractor,
    PdfFormulaSemanticReconstructor,
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


def test_display_formula_regions_merge_split_fraction_and_equation_label() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(612, 792),
        warnings=(),
        regions=(
            _text_region([PdfGlyph(ch, "NimbusRomNo9L-Regu", 10, (108 + i * 5, 450, 112 + i * 5, 460)) for i, ch in enumerate("before prose")]),
            _text_region([
                PdfGlyph("A", "CMR10", 10, (220, 473, 228, 483)),
                PdfGlyph("(", "CMR10", 10, (228, 473, 232, 483)),
                PdfGlyph("Q", "CMMI10", 10, (232, 473, 240, 483)),
                PdfGlyph(",", "CMR10", 10, (240, 473, 244, 483)),
                PdfGlyph("K", "CMMI10", 10, (246, 473, 254, 483)),
                PdfGlyph(")", "CMR10", 10, (254, 473, 258, 483)),
                PdfGlyph("=", "CMR10", 10, (262, 473, 270, 483)),
                PdfGlyph("s", "CMR10", 10, (274, 473, 280, 483)),
                PdfGlyph("(", "CMR10", 10, (280, 473, 284, 483)),
                PdfGlyph("Q", "CMMI10", 10, (286, 466, 294, 476)),
                PdfGlyph("K", "CMMI10", 10, (294, 466, 302, 476)),
                PdfGlyph("T", "CMMI7", 7, (303, 465, 308, 472)),
            ]),
            _text_region([PdfGlyph("√", "CMSY10", 10, (290, 473, 298, 483))]),
            _text_region([
                PdfGlyph("d", "CMMI10", 10, (298, 480, 306, 490)),
                PdfGlyph("k", "CMMI7", 7, (306, 484, 311, 491)),
                PdfGlyph(")", "CMR10", 10, (314, 473, 318, 483)),
                PdfGlyph("V", "CMMI10", 10, (318, 473, 326, 483)),
            ]),
            _text_region([PdfGlyph(ch, "NimbusRomNo9L-Regu", 10, (493 + i * 4, 473, 497 + i * 4, 483)) for i, ch in enumerate("(1)")]),
            _text_region([PdfGlyph(ch, "NimbusRomNo9L-Regu", 10, (108 + i * 5, 501, 112 + i * 5, 511)) for i, ch in enumerate("after prose with words")]),
            PdfRegion(page_num=0, kind="vector", bbox=(286, 478, 310, 479)),
            PdfRegion(page_num=0, kind="vector", bbox=(298, 480, 310, 481)),
        ),
    )

    regions = BornDigitalMathAuditor(
        segmentation=FormulaSegmentationConfig(min_confidence=0.45),
    ).display_formula_regions(extracted)

    assert len(regions) == 1
    region = regions[0]
    assert region.source == "pdf_structure_display_region"
    assert "A(Q,K)=s(QKT" in region.text
    assert "√" in region.text
    assert "dk)V" in region.text
    assert "(1)" in region.text
    assert "before" not in region.text
    assert "after" not in region.text
    assert region.line_count == 4
    assert region.vector_count == 2
    assert region.confidence >= 0.45


def test_display_formula_regions_reject_inline_math_prose() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(612, 792),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("W", "NimbusRomNo9L-Regu", 10, (108, 500, 116, 510)),
                PdfGlyph("e", "NimbusRomNo9L-Regu", 10, (116, 500, 124, 510)),
                PdfGlyph(" ", "NimbusRomNo9L-Regu", 10, (124, 500, 128, 510)),
                PdfGlyph("h", "NimbusRomNo9L-Regu", 10, (128, 500, 136, 510)),
                PdfGlyph("a", "NimbusRomNo9L-Regu", 10, (136, 500, 144, 510)),
                PdfGlyph("v", "NimbusRomNo9L-Regu", 10, (144, 500, 152, 510)),
                PdfGlyph("e", "NimbusRomNo9L-Regu", 10, (152, 500, 160, 510)),
                PdfGlyph(" ", "NimbusRomNo9L-Regu", 10, (160, 500, 164, 510)),
                PdfGlyph("x", "CMMI10", 10, (164, 500, 172, 510)),
                PdfGlyph("n", "CMMI7", 7, (172, 504, 177, 511)),
                PdfGlyph("→", "CMSY10", 10, (181, 500, 191, 510)),
                PdfGlyph("x", "CMMI10", 10, (194, 500, 202, 510)),
                PdfGlyph(" ", "NimbusRomNo9L-Regu", 10, (202, 500, 206, 510)),
                PdfGlyph("a", "NimbusRomNo9L-Regu", 10, (206, 500, 214, 510)),
                PdfGlyph("n", "NimbusRomNo9L-Regu", 10, (214, 500, 222, 510)),
                PdfGlyph("d", "NimbusRomNo9L-Regu", 10, (222, 500, 230, 510)),
                PdfGlyph(" ", "NimbusRomNo9L-Regu", 10, (230, 500, 234, 510)),
                PdfGlyph("continue", "NimbusRomNo9L-Regu", 10, (234, 500, 290, 510)),
            ]),
        ),
    )

    regions = BornDigitalMathAuditor().display_formula_regions(extracted)

    assert regions == []


def test_display_formula_regions_reject_math_footnote_author_line() -> None:
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(612, 792),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("A", "NimbusRomNo9L-Medi", 10, (248, 284, 256, 295)),
                PdfGlyph("i", "NimbusRomNo9L-Medi", 10, (256, 284, 260, 295)),
                PdfGlyph("d", "NimbusRomNo9L-Medi", 10, (260, 284, 268, 295)),
                PdfGlyph("a", "NimbusRomNo9L-Medi", 10, (268, 284, 276, 295)),
                PdfGlyph("n", "NimbusRomNo9L-Medi", 10, (276, 284, 284, 295)),
                PdfGlyph(" ", "NimbusRomNo9L-Medi", 10, (284, 284, 288, 295)),
                PdfGlyph("G", "NimbusRomNo9L-Medi", 10, (288, 284, 296, 295)),
                PdfGlyph("o", "NimbusRomNo9L-Medi", 10, (296, 284, 304, 295)),
                PdfGlyph("m", "NimbusRomNo9L-Medi", 10, (304, 284, 312, 295)),
                PdfGlyph("e", "NimbusRomNo9L-Medi", 10, (312, 284, 320, 295)),
                PdfGlyph("z", "NimbusRomNo9L-Medi", 10, (320, 284, 328, 295)),
                PdfGlyph("∗", "CMSY7", 7, (328, 284, 332, 291)),
                PdfGlyph("†", "CMSY7", 7, (333, 284, 337, 291)),
            ]),
        ),
    )

    regions = BornDigitalMathAuditor().display_formula_regions(extracted)

    assert regions == []


def test_display_formula_regions_reject_body_width_math_sentence() -> None:
    glyphs: list[PdfGlyph] = []
    text = "(x1, ..., xn) to another sequence of equal length (z1, ..., zn), with xi, zi ∈Rd"
    x = 108.0
    for ch in text:
        font = "CMMI10" if ch in {"x", "z", "i", "n", "R", "d"} else "NimbusRomNo9L-Regu"
        if ch == "∈":
            font = "CMSY10"
        glyphs.append(PdfGlyph(ch, font, 10, (x, 540, x + 4.6, 551)))
        x += 4.8
    extracted = BornDigitalPage(
        page_num=0,
        page_size=(612, 792),
        warnings=(),
        regions=(
            _text_region([PdfGlyph(ch, "NimbusRomNo9L-Regu", 10, (108 + i * 5, 510, 112 + i * 5, 521)) for i, ch in enumerate("ordinary body line with enough length")]),
            _text_region(glyphs),
        ),
    )

    regions = BornDigitalMathAuditor().display_formula_regions(extracted)

    assert regions == []


def test_formula_semantic_reconstructor_recovers_fraction_sqrt_and_scripts() -> None:
    page = BornDigitalPage(
        page_num=0,
        page_size=(612, 792),
        warnings=(),
        regions=(
            _text_region([
                PdfGlyph("A", "CMR10", 10, (220, 473, 228, 483)),
                PdfGlyph("(", "CMR10", 10, (228, 473, 232, 483)),
                PdfGlyph("Q", "CMMI10", 10, (232, 473, 240, 483)),
                PdfGlyph(",", "CMMI10", 10, (240, 473, 244, 483)),
                PdfGlyph("K", "CMMI10", 10, (246, 473, 254, 483)),
                PdfGlyph(")", "CMR10", 10, (254, 473, 258, 483)),
                PdfGlyph("=", "CMR10", 10, (262, 473, 270, 483)),
                PdfGlyph("s", "CMR10", 10, (274, 473, 280, 483)),
                PdfGlyph("(", "CMR10", 10, (280, 473, 284, 483)),
                PdfGlyph("Q", "CMMI10", 10, (286, 466, 294, 476)),
                PdfGlyph("K", "CMMI10", 10, (294, 466, 302, 476)),
                PdfGlyph("T", "CMMI7", 7, (303, 465, 308, 472)),
            ]),
            _text_region([
                PdfGlyph("√", "CMSY10", 10, (286, 473, 294, 483)),
                PdfGlyph("d", "CMMI10", 10, (296, 480, 304, 490)),
                PdfGlyph("k", "CMMI7", 7, (304, 484, 309, 491)),
                PdfGlyph(")", "CMR10", 10, (314, 473, 318, 483)),
                PdfGlyph("V", "CMMI10", 10, (318, 473, 326, 483)),
            ]),
            PdfRegion(page_num=0, kind="vector", bbox=(286, 478, 310, 479)),
        ),
    )
    region = BornDigitalMathAuditor(
        segmentation=FormulaSegmentationConfig(min_confidence=0.45),
    ).display_formula_regions(page)[0]

    result = PdfFormulaSemanticReconstructor().reconstruct(page, region)

    assert r"\frac{" in result.latex
    assert r"Q K^{T}" in result.latex
    assert r"\sqrt{d_{k}}" in result.latex
    assert "fraction_vector" in result.evidence
    assert result.warnings == ()


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
