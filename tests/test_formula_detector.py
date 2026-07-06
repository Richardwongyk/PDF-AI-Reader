from src.core.formula_detector import Pix2TextMFDDetector
from src.core.models import BlockType, DocumentBlock


def test_mfd_candidate_pages_include_image_blocks() -> None:
    detector = Pix2TextMFDDetector()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(10, 10, 100, 100),
        )
    ]

    assert detector._page_has_formulas(blocks, 0) is True


def test_mfd_candidate_pages_are_ranked_and_budgeted() -> None:
    detector = Pix2TextMFDDetector(max_mfd_pages=2)
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.PARAGRAPH,
            content="plain text with a=b",
            bbox=(0, 0, 100, 20),
        ),
        DocumentBlock(
            id="p4_b0",
            page_num=4,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 100, 100),
        ),
        DocumentBlock(
            id="p2_b0",
            page_num=2,
            block_type=BlockType.FORMULA,
            content=r"x=\frac{a}{b}",
            bbox=(0, 0, 100, 20),
        ),
    ]

    assert detector._rank_candidate_pages(blocks, [0, 2, 4]) == [2, 4]


def test_mfd_apply_limits_candidate_pages(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_mfd_pages=1)
    doc = type("FakeDoc", (), {"page_count": 3})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 100, 100),
        ),
        DocumentBlock(
            id="p1_b0",
            page_num=1,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 100, 100),
        ),
    ]
    seen_pages: list[list[int]] = []

    def fake_detect(doc: object, pages: list[int]) -> list[dict[str, object]]:
        seen_pages.append(pages)
        return []

    monkeypatch.setattr(detector, "detect_specific_pages", fake_detect)

    detector.apply_to_blocks(blocks, doc=doc)

    assert seen_pages == [[0]]


def test_mfd_apply_adds_unmatched_scanned_formula_block(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_mfd_pages=1)
    doc = type("FakeDoc", (), {"page_count": 1})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 300, 300),
        )
    ]

    monkeypatch.setattr(
        detector,
        "detect_specific_pages",
        lambda doc, pages: [{
            "page": 0,
            "bbox": (50.0, 50.0, 120.0, 90.0),
            "latex": None,
            "score": 0.95,
        }],
    )

    refined = detector.apply_to_blocks(blocks, doc=doc)

    formulas = [b for b in refined if b.block_type == BlockType.FORMULA]
    assert len(formulas) == 1
    assert formulas[0].metadata["source"] == "image_or_scan"
    assert formulas[0].metadata["needs_ocr"] is True


def test_mfd_apply_deduplicates_overlapping_scanned_formula_blocks(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_mfd_pages=1)
    doc = type("FakeDoc", (), {"page_count": 1})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 300, 300),
        )
    ]

    monkeypatch.setattr(
        detector,
        "detect_specific_pages",
        lambda doc, pages: [
            {
                "page": 0,
                "bbox": (50.0, 50.0, 120.0, 90.0),
                "latex": None,
                "score": 0.75,
            },
            {
                "page": 0,
                "bbox": (50.0, 50.0, 120.0, 90.0),
                "latex": None,
                "score": 0.95,
            },
        ],
    )
    seen_batches: list[list[dict[str, object]]] = []
    monkeypatch.setattr(
        detector,
        "_recognize_scanned_formulas",
        lambda doc, formulas: seen_batches.append(formulas) or {},
    )

    refined = detector.apply_to_blocks(blocks, doc=doc)

    formulas = [b for b in refined if b.block_type == BlockType.FORMULA]
    assert len(formulas) == 1
    assert formulas[0].metadata["formula_score"] == 0.95
    assert len(seen_batches[0]) == 1


def test_mfd_apply_recognizes_scanned_formula_latex(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_mfd_pages=1)
    doc = type("FakeDoc", (), {"page_count": 1})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 300, 300),
        )
    ]

    monkeypatch.setattr(
        detector,
        "detect_specific_pages",
        lambda doc, pages: [{
            "page": 0,
            "bbox": (50.0, 50.0, 120.0, 90.0),
            "latex": None,
            "score": 0.95,
        }],
    )
    monkeypatch.setattr(
        detector,
        "_recognize_scanned_formulas",
        lambda doc, formulas: {0: r"\frac{a}{b}"},
    )

    refined = detector.apply_to_blocks(blocks, doc=doc)

    formulas = [b for b in refined if b.block_type == BlockType.FORMULA]
    assert len(formulas) == 1
    assert formulas[0].content == "$$\n\\frac{a}{b}\n$$"
    assert formulas[0].metadata["needs_ocr"] is False
    assert formulas[0].metadata["mfr_recognized"] is True


def test_mfd_apply_preserves_each_new_formula_bbox(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_mfd_pages=1)
    doc = type("FakeDoc", (), {"page_count": 1})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 300, 300),
        )
    ]

    monkeypatch.setattr(
        detector,
        "detect_specific_pages",
        lambda doc, pages: [
            {
                "page": 0,
                "bbox": (10.0, 10.0, 80.0, 30.0),
                "latex": None,
                "score": 0.95,
            },
            {
                "page": 0,
                "bbox": (120.0, 50.0, 180.0, 90.0),
                "latex": None,
                "score": 0.90,
            },
        ],
    )
    monkeypatch.setattr(detector, "_recognize_scanned_formulas", lambda doc, formulas: {})

    refined = detector.apply_to_blocks(blocks, doc=doc)

    formulas = [b for b in refined if b.block_type == BlockType.FORMULA]
    assert [b.bbox for b in formulas] == [
        (10.0, 10.0, 80.0, 30.0),
        (120.0, 50.0, 180.0, 90.0),
    ]


def test_mfd_apply_recognizes_existing_non_latex_formula_block(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_existing_ocr_blocks=2, max_mfd_pages=1)
    doc = type("FakeDoc", (), {"page_count": 1})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.FORMULA,
            content="Attention( Q, K, V ) = softmax( QK T",
            bbox=(0, 0, 300, 50),
        )
    ]

    monkeypatch.setattr(
        detector,
        "_recognize_existing_formula_blocks",
        lambda doc, blocks: {
            "p0_b0": r"\mathrm{A t t e n t i o n}(Q,K,V)=\frac{QK^T}{\sqrt{d_k}}"
        },
    )
    monkeypatch.setattr(detector, "detect_specific_pages", lambda doc, pages: [])

    refined = detector.apply_to_blocks(blocks, doc=doc)

    formula = refined[0]
    assert formula.content == "$$\n\\mathrm{Attention}(Q,K,V)=\\frac{QK^T}{\\sqrt{d_k}}\n$$"
    assert formula.metadata["latex_source"] == "existing_block_ocr"
    assert formula.metadata["needs_ocr"] is False


def test_document_chunker_rejects_math_font_long_prose() -> None:
    from src.core.pdf_engine import DocumentChunker

    chunker = DocumentChunker()
    spans = [
        {
            "text": (
                "If M = N = R, we get R2, the Euclidean plane. "
                "The metric d Euclid is the one we started with, "
                "but using either of the other two metrics also makes sense."
            ),
            "font": "CMR10",
        }
    ]

    assert chunker._is_formula_from_spans(spans) is False


def test_document_chunker_rejects_proof_sentence_with_math_symbols() -> None:
    from src.core.pdf_engine import DocumentChunker

    chunker = DocumentChunker()
    spans = [
        {
            "text": (
                "Proof. We have d max ((x,y),(xn,yn)) = max {dM(x,xn), dN(y,yn)}."
            ),
            "font": "CMR10",
        }
    ]

    assert chunker._is_formula_from_spans(spans) is False


def test_document_chunker_rejects_figure_caption_with_math_symbols() -> None:
    from src.core.pdf_engine import DocumentChunker

    chunker = DocumentChunker()
    spans = [
        {
            "text": "Figure 2.1: The set of points x2 + y2 < 1 in R2 is open in R2.",
            "font": "CMR10",
        }
    ]

    assert chunker._is_formula_from_spans(spans) is False


def test_document_chunker_accepts_short_math_font_formula() -> None:
    from src.core.pdf_engine import DocumentChunker

    chunker = DocumentChunker()
    spans = [
        {
            "text": "Attention(Q,K,V)=softmax(QK^T/sqrt(d_k))V",
            "font": "CMMI10",
        }
    ]

    assert chunker._is_formula_from_spans(spans) is True


def test_math_text_wrapping_helpers_use_latex_delimiters() -> None:
    from src.core.models import BlockType, DocumentBlock, document_block_index_text, wrap_math_text

    assert wrap_math_text(r"\frac{a}{b}", display=True) == "$$\n\\frac{a}{b}\n$$"
    assert wrap_math_text("x+y", display=False) == r"\(x+y\)"
    already = "$$\nx+y\n$$"
    assert wrap_math_text(already, display=True) == already

    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"\alpha+\beta",
        bbox=(0, 0, 10, 10),
    )
    assert document_block_index_text(block) == "$$\n\\alpha+\\beta\n$$"

    shadowed = DocumentBlock(
        id="p0_b1",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="duplicate formula text",
        bbox=(0, 0, 10, 10),
        metadata={"shadowed_by": "born_digital_display_formula"},
    )
    assert document_block_index_text(shadowed) == ""


def test_document_chunker_wraps_math_font_spans_inline() -> None:
    from src.core.pdf_engine import DocumentChunker

    spans = [
        {"text": "dimension", "font": "NimbusRomNo9L-Regu", "size": 10.0, "bbox": (0, 0, 40, 10)},
        {"text": " d", "font": "CMMI10", "size": 10.0, "bbox": (40, 0, 47, 10)},
        {"text": "k", "font": "CMMI7", "size": 7.0, "bbox": (47, 4, 50, 11)},
        {"text": ", and apply", "font": "NimbusRomNo9L-Regu", "size": 10.0, "bbox": (50, 0, 90, 10)},
        {"text": "√", "font": "CMSY10", "size": 10.0, "bbox": (90, 0, 96, 10)},
        {"text": "d", "font": "CMMI10", "size": 10.0, "bbox": (96, 0, 101, 10)},
        {"text": "k", "font": "CMMI7", "size": 7.0, "bbox": (101, 4, 104, 11)},
        {"text": ".", "font": "NimbusRomNo9L-Regu", "size": 10.0, "bbox": (104, 0, 106, 10)},
    ]

    wrapped, evidence = DocumentChunker._text_with_inline_math_spans_with_evidence(spans)

    assert r"\(dk\)" in wrapped
    assert r"\(√dk\)" in wrapped
    assert wrapped.endswith(".")
    assert evidence[0]["latex"] == "dk"
    assert evidence[0]["has_script_size"] is True
    assert evidence[0]["font_size_min"] == 7.0
    assert evidence[0]["font_size_max"] == 10.0
    assert evidence[0]["spans"][0]["font"] == "CMMI10"


def test_document_chunker_wraps_broad_math_font_families_inline() -> None:
    from src.core.pdf_engine import DocumentChunker

    spans = [
        {"text": "for", "font": "NimbusRomNo9L-Regu"},
        {"text": "𝒜", "font": "STIXTwoMath-Regular"},
        {"text": " and", "font": "NimbusRomNo9L-Regu"},
        {"text": " x", "font": "LatinModernMath-Regular"},
        {"text": ", use", "font": "NimbusRomNo9L-Regu"},
        {"text": " y", "font": "Asana-Math"},
        {"text": ".", "font": "NimbusRomNo9L-Regu"},
    ]

    wrapped = DocumentChunker._text_with_inline_math_spans(spans)

    assert r"\(𝒜\)" in wrapped
    assert r"\(x\)" in wrapped
    assert r"\(y\)" in wrapped


def test_document_chunker_does_not_wrap_pure_inline_footnote_marks() -> None:
    from src.core.pdf_engine import DocumentChunker

    spans = [
        {"text": "Authors", "font": "NimbusRomNo9L-Regu"},
        {"text": "∗†", "font": "CMSY10"},
        {"text": " used", "font": "NimbusRomNo9L-Regu"},
        {"text": " x", "font": "CMMI10"},
        {"text": ".", "font": "NimbusRomNo9L-Regu"},
    ]

    wrapped = DocumentChunker._text_with_inline_math_spans(spans)

    assert r"\(∗†\)" not in wrapped
    assert "∗†" in wrapped
    assert r"\(x\)" in wrapped


def test_existing_formula_ocr_rejects_long_prose_with_math_symbols() -> None:
    detector = Pix2TextMFDDetector(max_existing_ocr_blocks=2)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=(
            "Where the projections are parameter matrices W Q i in R d model x d k, "
            "W K i in R d model x d k, W V i in R d model x d v and W O in R h d v x d model."
        ),
        bbox=(0, 0, 100, 20),
    )

    assert detector._should_ocr_existing_formula_block(block) is False


def test_formula_production_paths_do_not_use_sample_word_gates() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    checked_files = [
        root / "src/core/formula_detector.py",
        root / "src/core/born_digital_formula_extractor.py",
        root / "tools/formula_multiround_pipeline.py",
    ]
    banned = ("Attention", "softmax", "FFN", "BLEU", "EN-DE", "EN-FR", "lrate")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in checked_files)

    assert not any(token in combined for token in banned)


def test_r0_structure_extractor_does_not_default_to_handwritten_latex_reconstruction() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/core/born_digital_formula_extractor.py").read_text(encoding="utf-8")

    assert "PdfFormulaSemanticReconstructor" not in source
    assert "pymupdf_rawdict_facts_v1" in source


def test_normalize_latex_collapses_spaced_text_commands() -> None:
    detector = Pix2TextMFDDetector()

    assert (
        detector._normalize_latex(r"\mathrm{A t t e n t i o n} (Q)=\cfrac{a}{b}")
        == r"\mathrm{Attention} (Q)=\frac{a}{b}"
    )


def test_formula_audit_similarity_matches_latex_variants() -> None:
    from tools.formula_latex_audit import _best_formula_matches, _normalize_formula_for_match

    source = r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(\frac{QK^T}{\sqrt{d_k}})V"
    extracted = r"\mathrm{Attention} ( Q , K , V )=\mathrm{softmax} ( \frac{Q K^{T}} {\sqrt{d_{k}}} )"

    assert _normalize_formula_for_match(r"\dmodel + \RR") == "d_{model}+r"
    matches, low_pdf, metrics = _best_formula_matches([source], [extracted])

    assert matches[0]["similarity"] >= 0.65
    assert metrics["weak"] == 1
    assert low_pdf == []


def test_formula_audit_canonicalizes_upright_text_commands() -> None:
    from tools.formula_latex_audit import _command_counts

    counts = _command_counts([r"\text{where}+\operatorname{softmax}(x)+\mathrm{A}"])

    assert counts[r"\mathrm"] == 3
    assert r"\text" not in counts
    assert r"\operatorname" not in counts


def test_formula_audit_ignores_latex_line_break_spacing(tmp_path) -> None:
    from tools.formula_latex_audit import _extract_source_formulas

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(
        r"""
        \begin{document}
        ordinary prose \\[1ex]
        \[x+y=1\]
        \end{document}
        """,
        encoding="utf-8",
    )

    display, inline, _count = _extract_source_formulas(latex_root)

    assert display == ["x+y=1"]
    assert inline == []


def test_formula_audit_treats_all_latex_math_delimiters_as_formulas(tmp_path) -> None:
    from tools.formula_latex_audit import _extract_source_formulas

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(
        r"""
        \begin{document}
        inline dollar $x_i$ and inline bracket \(y_j\).
        display dollar $$a=b$$
        display bracket \[c=d\]
        \end{document}
        """,
        encoding="utf-8",
    )

    display, inline, _count = _extract_source_formulas(latex_root)

    assert display == ["a=b", "c=d"]
    assert inline == ["x_i", "y_j"]


def test_formula_latex_audit_can_match_display_scope(monkeypatch, tmp_path) -> None:
    from tools import formula_latex_audit as audit

    case = audit.CasePaths(
        name="sample",
        pdf=tmp_path / "paper.pdf",
        latex_root=tmp_path / "latex",
    )
    case.pdf.write_bytes(b"%PDF-placeholder")
    case.latex_root.mkdir()
    (case.latex_root / "main.tex").write_text(
        r"\[x+y=1\] inline $z_n$",
        encoding="utf-8",
    )
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$x+y=1$$",
            bbox=(0, 0, 100, 20),
        )
    ]
    monkeypatch.setattr(audit, "_parse_pdf_blocks_limited", lambda *args, **kwargs: (1, blocks))

    display_report = audit._audit_case(
        case,
        run_mfd=False,
        mfd_pages=None,
        match_scope="display",
    )
    all_report = audit._audit_case(
        case,
        run_mfd=False,
        mfd_pages=None,
        match_scope="all",
    )

    assert display_report.source_formula_snippets == 1
    assert all_report.source_formula_snippets == 2
    assert all_report.pdf_inline_formula_snippets == 0


def test_formula_latex_audit_counts_inline_pdf_candidates(monkeypatch, tmp_path) -> None:
    from tools import formula_latex_audit as audit

    case = audit.CasePaths(
        name="sample",
        pdf=tmp_path / "paper.pdf",
        latex_root=tmp_path / "latex",
    )
    case.pdf.write_bytes(b"%PDF-placeholder")
    case.latex_root.mkdir()
    (case.latex_root / "main.tex").write_text(r"inline \(z_n\)", encoding="utf-8")
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.PARAGRAPH,
            content=r"inline \(z_n\)",
            bbox=(0, 0, 100, 20),
        )
    ]
    monkeypatch.setattr(audit, "_parse_pdf_blocks_limited", lambda *args, **kwargs: (1, blocks))

    report = audit._audit_case(case, run_mfd=False, mfd_pages=None)

    assert report.pdf_formula_blocks == 0
    assert report.pdf_inline_formula_snippets == 1
    assert report.pdf_formula_candidate_snippets == 1
    assert report.inline_source_near_match_rate >= 0.99
    assert report.sample_pdf_inline_formulas == ["z_n"]


def test_formula_audit_limited_parse_uses_page_budget(monkeypatch, tmp_path) -> None:
    from tools import formula_latex_audit as audit

    class FakeDoc:
        page_count = 5

        def __getitem__(self, page_num: int) -> object:
            return object()

        def close(self) -> None:
            pass

    seen_pages: list[int] = []
    init_args: list[dict[str, bool]] = []

    class FakeChunker:
        def __init__(
            self,
            enable_born_digital_math: bool = False,
            enable_born_digital_semantics: bool = False,
            enable_legacy_formula_heuristic: bool = True,
        ) -> None:
            init_args.append({
                "born_digital_math": enable_born_digital_math,
                "born_digital_semantics": enable_born_digital_semantics,
                "legacy_formula_heuristic": enable_legacy_formula_heuristic,
            })

        def chunk_page(self, doc: object, page_num: int) -> list[DocumentBlock]:
            seen_pages.append(page_num)
            return []

    monkeypatch.setattr(audit.fitz, "open", lambda pdf: FakeDoc())
    monkeypatch.setattr(audit, "DocumentChunker", FakeChunker)

    page_count, blocks = audit._parse_pdf_blocks_limited(
        tmp_path / "paper.pdf",
        run_mfd=False,
        mfd_pages=None,
        max_pages=2,
        born_digital_math=True,
        born_digital_semantics=True,
        legacy_formula_heuristic=False,
    )

    assert page_count == 5
    assert blocks == []
    assert seen_pages == [0, 1]
    assert init_args == [{
        "born_digital_math": True,
        "born_digital_semantics": True,
        "legacy_formula_heuristic": False,
    }]


def test_formula_audit_quality_gate_flags_low_recall(monkeypatch, tmp_path) -> None:
    from tools import formula_latex_audit as audit

    case = audit.CasePaths(
        name="sample",
        pdf=tmp_path / "paper.pdf",
        latex_root=tmp_path / "latex",
    )
    case.pdf.write_bytes(b"%PDF-placeholder")
    case.latex_root.mkdir()
    (case.latex_root / "main.tex").write_text(
        r"$\frac{x}{y}$ $a \in \RR$",
        encoding="utf-8",
    )
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.FORMULA,
            content="plain text without source commands",
            bbox=(0, 0, 100, 20),
        )
    ]
    monkeypatch.setattr(audit, "_parse_pdf_blocks_limited", lambda *args, **kwargs: (1, blocks))

    report = audit._audit_case(
        case,
        run_mfd=False,
        mfd_pages=None,
        min_command_recall=0.5,
        min_weak_match_rate=0.5,
        max_low_similarity_pdf_rate=0.5,
    )

    assert report.quality_gate["passed"] is False
    assert report.quality_gate["violations"]


def test_document_chunker_can_append_born_digital_display_formula(monkeypatch) -> None:
    from src.core.pdf_engine import DocumentChunker

    class Region:
        bbox = (40.0, 60.0, 140.0, 78.0)
        text = "x+y=1"
        source = "pdf_structure_display_region"
        confidence = 0.88
        evidence = ("math_font", "centered_line")
        line_count = 1
        vector_count = 0

    class FakeAuditor:
        def display_formula_regions(self, page: object) -> list[Region]:
            return [Region()]

    class FakeExtractor:
        def extract_page(self, page: object, page_num: int) -> object:
            return object()

    monkeypatch.setattr("src.core.born_digital_math.BornDigitalMathAuditor", lambda: FakeAuditor())
    monkeypatch.setattr("src.core.born_digital_math.MuPDFBornDigitalExtractor", lambda: FakeExtractor())

    chunker = DocumentChunker(enable_born_digital_math=True)
    blocks = chunker._append_born_digital_display_formulas(object(), 0, [])

    assert len(blocks) == 1
    block = blocks[0]
    assert block.block_type == BlockType.FORMULA
    assert block.content == "$$\nx+y=1\n$$"
    assert block.metadata["source"] == "pdf_structure_display_region"
    assert block.metadata["needs_ocr"] is False
    assert block.metadata["semantic_recovery"] == "pending"


def test_document_chunker_marks_shadowed_paragraph_for_born_digital_formula(monkeypatch) -> None:
    from src.core.pdf_engine import DocumentChunker

    class Region:
        bbox = (40.0, 60.0, 140.0, 78.0)
        text = "x+y=1"
        source = "pdf_structure_display_region"
        confidence = 0.88
        evidence = ("math_font",)
        line_count = 1
        vector_count = 0

    class FakeAuditor:
        def display_formula_regions(self, page: object) -> list[Region]:
            return [Region()]

    class FakeExtractor:
        def extract_page(self, page: object, page_num: int) -> object:
            return object()

    monkeypatch.setattr("src.core.born_digital_math.BornDigitalMathAuditor", lambda: FakeAuditor())
    monkeypatch.setattr("src.core.born_digital_math.MuPDFBornDigitalExtractor", lambda: FakeExtractor())

    paragraph = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="x+y=1",
        bbox=(40.0, 60.0, 140.0, 78.0),
    )

    blocks = DocumentChunker(enable_born_digital_math=True)._append_born_digital_display_formulas(
        object(),
        0,
        [paragraph],
    )

    assert paragraph.metadata["shadowed_by"] == "born_digital_display_formula"
    assert [block.block_type for block in blocks].count(BlockType.FORMULA) == 1


def test_math_ocr_uses_cache_before_loading_model(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    image = b"fake-png-bytes"
    cache.put(cache.hash_image(image), r"\frac{a}{b}", "pix2text-mfr")

    ocr = MathOCR()
    ocr._cache = cache

    def fail_load() -> None:
        raise AssertionError("model should not be loaded on cache hit")

    monkeypatch.setattr(ocr, "_ensure_model", fail_load)
    monkeypatch.setattr(
        type(ocr),
        "is_available",
        property(lambda self: (_ for _ in ()).throw(AssertionError("availability should not be checked"))),
    )

    assert ocr.recognize(image) == r"\frac{a}{b}"


def test_formula_recognizer_registry_exposes_default_backend() -> None:
    from src.core.formula_recognizers import FormulaRecognizerRegistry

    recognizer = FormulaRecognizerRegistry.create("pix2text-mfr", batch_size=2, num_threads=1)

    assert recognizer.name == "pix2text-mfr"
    assert "pix2text-mfr" in FormulaRecognizerRegistry.available_names()


def test_pix2text_formula_recognizer_uses_batch_api_and_score() -> None:
    import io

    from PIL import Image

    from src.core.formula_recognizers import Pix2TextFormulaRecognizer

    calls: list[dict[str, object]] = []

    class FakePix2Text:
        def recognize_formula(self, paths: list[str], **kwargs: object) -> list[dict[str, object]]:
            calls.append({"paths": paths, **kwargs})
            return [
                {"text": r"\alpha+\beta", "score": 0.91},
                {"text": r"\frac{a}{b}", "score": "0.82"},
            ]

    def png_bytes() -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    recognizer = Pix2TextFormulaRecognizer(batch_size=8, num_threads=1)
    recognizer._p2t = FakePix2Text()

    results = recognizer.recognize_batch_with_metadata([png_bytes(), png_bytes()])

    assert [result.latex for result in results] == [r"\alpha+\beta", r"\frac{a}{b}"]
    assert [result.score for result in results] == [0.91, 0.82]
    assert calls[0]["batch_size"] == 8
    assert calls[0]["return_text"] is False
    assert len(calls[0]["paths"]) == 2


def test_pix2text_formula_recognizer_falls_back_for_old_api() -> None:
    import io

    from PIL import Image

    from src.core.formula_recognizers import Pix2TextFormulaRecognizer

    calls: list[object] = []

    class FakeOldPix2Text:
        def recognize_formula(self, paths: list[str], *args: object, **kwargs: object) -> list[str]:
            calls.append((list(paths), args, dict(kwargs)))
            if "return_text" in kwargs:
                raise TypeError("old api")
            return [r"\gamma"]

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="PNG")
    recognizer = Pix2TextFormulaRecognizer(batch_size=3, num_threads=1)
    recognizer._p2t = FakeOldPix2Text()

    assert recognizer.recognize_batch([buffer.getvalue()]) == [r"\gamma"]
    assert len(calls) == 2
    assert calls[0][2]["return_text"] is False
    assert calls[1][2]["batch_size"] == 3


def test_pix2text_formula_recognizer_normalizes_nested_outputs() -> None:
    from src.core.formula_recognizers import Pix2TextFormulaRecognizer

    class ResultObject:
        rec_text = r"\int_0^1 x\,dx"
        probability = "0.77"

    results = Pix2TextFormulaRecognizer._normalize_outputs(
        [
            {"res": {"rec_formula": r"\sqrt{x}", "confidence": "0.73"}},
            ResultObject(),
        ],
        expected=3,
    )

    assert [result.latex for result in results] == [
        r"\sqrt{x}",
        r"\int_0^1 x\,dx",
        "",
    ]
    assert [result.score for result in results] == [0.73, 0.77, None]


def test_formula_ocr_benchmark_marks_pure_formula_samples() -> None:
    from tools.formula_ocr_benchmark import _formula_sample_profile

    short_formula = DocumentBlock(
        id="f0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"x_n \to x",
        bbox=(0, 0, 10, 10),
    )
    mixed_prose = DocumentBlock(
        id="f1",
        page_num=0,
        block_type=BlockType.FORMULA,
        content="We have (x_n, y_n) -> (x, y) if and only if x_n -> x.",
        bbox=(0, 0, 10, 10),
    )

    assert _formula_sample_profile(short_formula).selection_reason == "pure_formula_like"
    assert _formula_sample_profile(mixed_prose).selection_reason == "mixed_text_formula"


def test_formula_ocr_benchmark_short_mixed_formula_is_not_pure() -> None:
    from tools.formula_ocr_benchmark import _formula_sample_profile

    block = DocumentBlock(
        id="f0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content="head_i = Attention(QW_i^Q, KW_i^K, VW_i^V)",
        bbox=(0, 0, 10, 10),
    )

    profile = _formula_sample_profile(block)

    assert profile.selection_reason == "mixed_but_short"
    assert profile.words <= 8
    assert profile.math_markers >= 2


def test_formula_ocr_benchmark_plain_symbol_equation_is_pure() -> None:
    from tools.formula_ocr_benchmark import _formula_sample_profile

    block = DocumentBlock(
        id="f0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content="x+y=1",
        bbox=(0, 0, 10, 10),
    )

    assert _formula_sample_profile(block).selection_reason == "pure_formula_like"


def test_formula_ocr_benchmark_long_latex_prose_is_not_pure() -> None:
    from tools.formula_ocr_benchmark import _formula_sample_profile

    block = DocumentBlock(
        id="f0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"Given vectors \alpha and \beta, the equation is used in the proof.",
        bbox=(0, 0, 10, 10),
    )

    assert _formula_sample_profile(block).selection_reason == "mixed_text_formula"


def test_formula_ocr_benchmark_pure_formula_filter_excludes_mixed_blocks(monkeypatch) -> None:
    from pathlib import Path

    from tools import formula_ocr_benchmark as benchmark

    blocks = [
        DocumentBlock(
            id="pure",
            page_num=0,
            block_type=BlockType.FORMULA,
            content="x+y=1",
            bbox=(0, 0, 10, 10),
        ),
        DocumentBlock(
            id="mixed",
            page_num=0,
            block_type=BlockType.FORMULA,
            content="head_i = Attention(QW_i^Q, KW_i^K, VW_i^V)",
            bbox=(0, 0, 10, 10),
        ),
        DocumentBlock(
            id="prose",
            page_num=0,
            block_type=BlockType.FORMULA,
            content="We use x_n -> x after taking the limit.",
            bbox=(0, 0, 10, 10),
        ),
    ]

    class FakeDoc:
        page_count = 1

        def close(self) -> None:
            return None

    class FakeChunker:
        def chunk_page(self, doc: object, page_num: int) -> list[DocumentBlock]:
            return blocks

    monkeypatch.setattr(benchmark.fitz, "open", lambda pdf: FakeDoc())
    monkeypatch.setattr(benchmark, "DocumentChunker", lambda: FakeChunker())

    _, filtered, _ = benchmark._parse_formula_blocks(
        Path("fake.pdf"),
        start_page=0,
        max_pages=1,
        sample_limit=10,
        pure_formula_only=True,
    )
    _, unfiltered, _ = benchmark._parse_formula_blocks(
        Path("fake.pdf"),
        start_page=0,
        max_pages=1,
        sample_limit=10,
        pure_formula_only=False,
    )

    assert [block.id for block in filtered] == ["pure"]
    assert [block.id for block in unfiltered] == ["pure", "mixed", "prose"]


def test_formula_ocr_cache_is_model_scoped(tmp_path) -> None:
    from src.core.math_ocr import _FormulaOcrCache

    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    image_hash = cache.hash_image(b"same-image")
    cache.put(image_hash, r"\alpha", "pix2text-mfr")
    cache.put(image_hash, r"\beta", "future-backend")

    assert cache.get(image_hash, "pix2text-mfr") == r"\alpha"
    assert cache.get(image_hash, "future-backend") == r"\beta"


def test_formula_ocr_cache_migrates_legacy_image_hash_primary_key(tmp_path) -> None:
    import sqlite3
    from datetime import datetime, timezone

    from src.core.math_ocr import _FormulaOcrCache

    db_path = tmp_path / "legacy_cache.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE formula_ocr_cache (
            image_hash TEXT PRIMARY KEY,
            latex TEXT NOT NULL,
            model TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO formula_ocr_cache (image_hash, latex, model, created_at) VALUES (?, ?, ?, ?)",
        ("abc", r"\gamma", "pix2text-mfr", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()

    cache = _FormulaOcrCache(str(db_path))

    assert cache.get("abc", "pix2text-mfr") == r"\gamma"
    cache.put("abc", r"\delta", "future-backend")
    assert cache.get("abc", "pix2text-mfr") == r"\gamma"
    assert cache.get("abc", "future-backend") == r"\delta"


def test_math_ocr_uses_process_default_backend(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    MathOCR.set_default_backend("pix2text")
    ocr = MathOCR()
    ocr._cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))

    assert ocr.backend_name == "pix2text"

    MathOCR._instance = None
    MathOCR.set_default_backend("pix2text-mfr")


def test_math_ocr_cache_uses_recognizer_namespace(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    class FakeRecognizer:
        name = "local_formula_model"
        cache_namespace = "local_formula_model:m1"

        @property
        def is_available(self) -> bool:
            return True

        def recognize_batch(self, images: list[bytes]) -> list[str]:
            return [r"\theta"]

    MathOCR._instance = None
    MathOCR.set_default_backend_config(
        "local_formula_model",
        model_name="m1",
    )
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    image = b"same-image"
    cache.put(cache.hash_image(image), r"\alpha", "local_formula_model:m0")
    ocr = MathOCR()
    ocr._cache = cache
    monkeypatch.setattr(ocr, "_get_recognizer", lambda: FakeRecognizer())

    assert ocr.recognize(image) == r"\theta"
    assert cache.get(cache.hash_image(image), "local_formula_model:m1") == r"\theta"
    assert cache.get(cache.hash_image(image), "local_formula_model:m0") == r"\alpha"

    MathOCR._instance = None
    MathOCR.set_default_backend("pix2text-mfr")


def test_math_ocr_limits_uncached_model_calls(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    cached_image = b"cached-png"
    cache.put(cache.hash_image(cached_image), r"\sqrt{x}", "pix2text-mfr")

    ocr = MathOCR()
    ocr._cache = cache
    called_batches: list[list[bytes]] = []

    monkeypatch.setattr(type(ocr), "is_available", property(lambda self: True))
    monkeypatch.setattr(ocr, "_ensure_model", lambda: None)

    def fake_recognize_batch_impl(images: list[bytes]) -> list[str]:
        called_batches.append(images)
        return [r"\frac{a}{b}" for _ in images]

    monkeypatch.setattr(ocr, "_recognize_batch_impl", fake_recognize_batch_impl)

    results = ocr.recognize_batch(
        [b"miss-1", cached_image, b"miss-2"],
        max_uncached=1,
    )

    assert called_batches == [[b"miss-1"]]
    assert results == [r"\frac{a}{b}", r"\sqrt{x}", ""]


def test_math_ocr_deduplicates_uncached_images_within_batch(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    ocr = MathOCR()
    ocr._cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    called_batches: list[list[bytes]] = []

    monkeypatch.setattr(type(ocr), "is_available", property(lambda self: True))
    monkeypatch.setattr(ocr, "_ensure_model", lambda: None)

    def fake_recognize_batch_impl(images: list[bytes]) -> list[str]:
        called_batches.append(images)
        return [r"\alpha", r"\beta"]

    monkeypatch.setattr(ocr, "_recognize_batch_impl", fake_recognize_batch_impl)

    results = ocr.recognize_batch(
        [b"same-image", b"same-image", b"other-image", b"same-image"],
        max_uncached=4,
    )

    assert called_batches == [[b"same-image", b"other-image"]]
    assert results == [r"\alpha", r"\alpha", r"\beta", r"\alpha"]


def test_math_ocr_uncached_budget_counts_duplicate_images_once(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    ocr = MathOCR()
    ocr._cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    called_batches: list[list[bytes]] = []

    monkeypatch.setattr(type(ocr), "is_available", property(lambda self: True))
    monkeypatch.setattr(ocr, "_ensure_model", lambda: None)

    def fake_recognize_batch_impl(images: list[bytes]) -> list[str]:
        called_batches.append(images)
        return [r"\alpha"]

    monkeypatch.setattr(ocr, "_recognize_batch_impl", fake_recognize_batch_impl)

    results = ocr.recognize_batch(
        [b"same-image", b"same-image", b"other-image"],
        max_uncached=1,
    )

    assert called_batches == [[b"same-image"]]
    assert results == [r"\alpha", r"\alpha", ""]


def test_math_ocr_zero_uncached_budget_uses_cache_only(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    cached_image = b"cached-png"
    cache.put(cache.hash_image(cached_image), r"\int f(x)\,dx", "pix2text-mfr")

    ocr = MathOCR()
    ocr._cache = cache

    monkeypatch.setattr(
        type(ocr),
        "is_available",
        property(lambda self: (_ for _ in ()).throw(AssertionError("availability should not be checked"))),
    )
    monkeypatch.setattr(
        ocr,
        "_recognize_batch_impl",
        lambda images: (_ for _ in ()).throw(AssertionError("model should not be called")),
    )

    assert ocr.recognize_batch([b"miss", cached_image], max_uncached=0) == [
        "",
        r"\int f(x)\,dx",
    ]


def test_math_ocr_metadata_cache_only_does_not_load_model(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    cached_image = b"cached-png"
    cache.put(cache.hash_image(cached_image), r"\sum_i x_i", "pix2text-mfr")

    ocr = MathOCR()
    ocr._cache = cache

    monkeypatch.setattr(
        type(ocr),
        "is_available",
        property(lambda self: (_ for _ in ()).throw(AssertionError("availability should not be checked"))),
    )
    monkeypatch.setattr(
        ocr,
        "_recognize_batch_with_metadata_impl",
        lambda images: (_ for _ in ()).throw(AssertionError("model should not be called")),
    )

    results = ocr.recognize_batch_with_metadata(
        [b"miss", cached_image],
        max_uncached=0,
    )

    assert [result.latex for result in results] == ["", r"\sum_i x_i"]
    assert results[1].score is None
    assert "cache_hit" in results[1].warnings


def test_math_ocr_metadata_deduplicates_uncached_images(monkeypatch, tmp_path) -> None:
    from src.core.formula_recognizers import FormulaRecognitionResult
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    ocr = MathOCR()
    ocr._cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    called_batches: list[list[bytes]] = []

    monkeypatch.setattr(type(ocr), "is_available", property(lambda self: True))
    monkeypatch.setattr(ocr, "_ensure_model", lambda: None)

    def fake_recognize_batch_impl(images: list[bytes]) -> list[FormulaRecognitionResult]:
        called_batches.append(images)
        return [
            FormulaRecognitionResult(latex=r"\alpha", score=0.9),
            FormulaRecognitionResult(latex=r"\beta", score=0.8),
        ]

    monkeypatch.setattr(
        ocr,
        "_recognize_batch_with_metadata_impl",
        fake_recognize_batch_impl,
    )

    results = ocr.recognize_batch_with_metadata(
        [b"same-image", b"same-image", b"other-image"],
        max_uncached=3,
    )

    assert called_batches == [[b"same-image", b"other-image"]]
    assert [result.latex for result in results] == [r"\alpha", r"\alpha", r"\beta"]
    assert [result.score for result in results] == [0.9, 0.9, 0.8]


def test_math_ocr_metadata_uncached_budget_counts_duplicate_images_once(
    monkeypatch,
    tmp_path,
) -> None:
    from src.core.formula_recognizers import FormulaRecognitionResult
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    ocr = MathOCR()
    ocr._cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    called_batches: list[list[bytes]] = []

    monkeypatch.setattr(type(ocr), "is_available", property(lambda self: True))
    monkeypatch.setattr(ocr, "_ensure_model", lambda: None)

    def fake_recognize_batch_impl(images: list[bytes]) -> list[FormulaRecognitionResult]:
        called_batches.append(images)
        return [FormulaRecognitionResult(latex=r"\alpha", score=0.9)]

    monkeypatch.setattr(
        ocr,
        "_recognize_batch_with_metadata_impl",
        fake_recognize_batch_impl,
    )

    results = ocr.recognize_batch_with_metadata(
        [b"same-image", b"same-image", b"other-image"],
        max_uncached=1,
    )

    assert called_batches == [[b"same-image"]]
    assert [result.latex for result in results] == [r"\alpha", r"\alpha", ""]
    assert [result.score for result in results] == [0.9, 0.9, None]


def test_math_ocr_metadata_writes_recognized_latex_to_cache(monkeypatch, tmp_path) -> None:
    from src.core.formula_recognizers import FormulaRecognitionResult
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    ocr = MathOCR()
    ocr._cache = cache

    monkeypatch.setattr(type(ocr), "is_available", property(lambda self: True))
    monkeypatch.setattr(ocr, "_ensure_model", lambda: None)
    monkeypatch.setattr(
        ocr,
        "_recognize_batch_with_metadata_impl",
        lambda images: [FormulaRecognitionResult(latex=r"\eta", score=0.7)],
    )

    results = ocr.recognize_batch_with_metadata([b"new-image"], max_uncached=1)
    image_hash = cache.hash_image(b"new-image")

    assert results[0].latex == r"\eta"
    assert results[0].score == 0.7
    assert cache.get(image_hash, "pix2text-mfr") == r"\eta"


def test_scanned_formula_ocr_budget_keeps_placeholders(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(
        max_scanned_ocr_blocks=1,
        max_scanned_uncached_ocr_blocks=1,
        max_mfd_pages=2,
    )
    doc = type("FakeDoc", (), {"page_count": 2})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 300, 300),
        ),
        DocumentBlock(
            id="p1_b0",
            page_num=1,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 300, 300),
        ),
    ]

    monkeypatch.setattr(
        detector,
        "detect_specific_pages",
        lambda doc, pages: [
            {
                "page": 1,
                "bbox": (50.0, 50.0, 120.0, 90.0),
                "latex": None,
                "score": 0.99,
            },
            {
                "page": 0,
                "bbox": (40.0, 40.0, 100.0, 80.0),
                "latex": None,
                "score": 0.80,
            },
        ],
    )
    monkeypatch.setattr(
        detector,
        "_crop_formula_image",
        lambda doc, formula: f"page-{formula['page']}".encode(),
    )

    calls: list[tuple[list[bytes], int | None]] = []

    class FakeMathOCR:
        def recognize_batch(
            self, images: list[bytes], max_uncached: int | None = None
        ) -> list[str]:
            calls.append((images, max_uncached))
            return [r"\alpha+\beta"] + [""] * (len(images) - 1)

    monkeypatch.setattr("src.core.math_ocr.MathOCR", FakeMathOCR)

    refined = detector.apply_to_blocks(blocks, doc=doc)
    formulas = [b for b in refined if b.block_type == BlockType.FORMULA]

    assert calls == [([b"page-0"], 1)]
    assert len(formulas) == 2
    recognized = [b for b in formulas if b.metadata["needs_ocr"] is False]
    pending = [b for b in formulas if b.metadata["needs_ocr"] is True]
    assert len(recognized) == 1
    assert recognized[0].page_num == 0
    assert recognized[0].content == "$$\n\\alpha+\\beta\n$$"
    assert len(pending) == 1
    assert pending[0].content == "[图片公式，等待 OCR 识别]"


def test_scanned_formula_ocr_defaults_to_cache_only(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_scanned_ocr_blocks=2)
    formulas = [
        {"page": 0, "bbox": (0.0, 0.0, 20.0, 20.0), "score": 0.9},
        {"page": 1, "bbox": (0.0, 0.0, 20.0, 20.0), "score": 0.8},
    ]

    monkeypatch.setattr(
        detector,
        "_crop_formula_image",
        lambda doc, formula: f"page-{formula['page']}".encode(),
    )
    calls: list[int | None] = []

    class FakeMathOCR:
        def recognize_batch(
            self, images: list[bytes], max_uncached: int | None = None
        ) -> list[str]:
            calls.append(max_uncached)
            return ["", ""]

    monkeypatch.setattr("src.core.math_ocr.MathOCR", FakeMathOCR)

    assert detector._recognize_scanned_formulas(object(), formulas) == {}
    assert calls == [0]


def test_mfd_default_skips_page_detection_for_interactive_parse(monkeypatch) -> None:
    detector = Pix2TextMFDDetector()
    doc = type("FakeDoc", (), {"page_count": 1})()
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.IMAGE,
            content="",
            bbox=(0, 0, 100, 100),
        )
    ]

    monkeypatch.setattr(
        detector,
        "detect_specific_pages",
        lambda doc, pages: (_ for _ in ()).throw(AssertionError("MFD should be skipped by default")),
    )

    detector.apply_to_blocks(blocks, doc)

    assert [block for block in blocks if block.block_type == BlockType.FORMULA] == []


def test_existing_formula_ocr_defaults_to_cache_only(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_existing_ocr_blocks=1)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content="Attention(Q,K,V)=softmax(QK T)",
        bbox=(0, 0, 100, 20),
    )

    monkeypatch.setattr(detector, "_crop_bbox_image", lambda *args, **kwargs: b"formula")
    calls: list[int | None] = []

    class FakeMathOCR:
        def recognize_batch(
            self, images: list[bytes], max_uncached: int | None = None
        ) -> list[str]:
            calls.append(max_uncached)
            return [""]

    monkeypatch.setattr("src.core.math_ocr.MathOCR", FakeMathOCR)

    assert detector._recognize_existing_formula_blocks(object(), [block]) == {}
    assert calls == [0]
