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
    assert formulas[0].content == r"\frac{a}{b}"
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
    assert formula.content == r"\mathrm{Attention}(Q,K,V)=\frac{QK^T}{\sqrt{d_k}}"
    assert formula.metadata["latex_source"] == "existing_block_ocr"
    assert formula.metadata["needs_ocr"] is False


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


def test_formula_audit_limited_parse_uses_page_budget(monkeypatch, tmp_path) -> None:
    from tools import formula_latex_audit as audit

    class FakeDoc:
        page_count = 5

        def __getitem__(self, page_num: int) -> object:
            return object()

        def close(self) -> None:
            pass

    seen_pages: list[int] = []

    class FakeChunker:
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
    )

    assert page_count == 5
    assert blocks == []
    assert seen_pages == [0, 1]


def test_math_ocr_uses_cache_before_loading_model(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    image = b"fake-png-bytes"
    cache.put(cache.hash_image(image), r"\frac{a}{b}", "test")

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


def test_math_ocr_limits_uncached_model_calls(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    cached_image = b"cached-png"
    cache.put(cache.hash_image(cached_image), r"\sqrt{x}", "test")

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


def test_math_ocr_zero_uncached_budget_uses_cache_only(monkeypatch, tmp_path) -> None:
    from src.core.math_ocr import MathOCR, _FormulaOcrCache

    MathOCR._instance = None
    cache = _FormulaOcrCache(str(tmp_path / "formula_cache.db"))
    cached_image = b"cached-png"
    cache.put(cache.hash_image(cached_image), r"\int f(x)\,dx", "test")

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
    assert recognized[0].content == r"\alpha+\beta"
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
