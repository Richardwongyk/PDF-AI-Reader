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


def test_mfd_apply_adds_unmatched_scanned_formula_block(monkeypatch) -> None:
    detector = Pix2TextMFDDetector()
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


def test_mfd_apply_recognizes_scanned_formula_latex(monkeypatch) -> None:
    detector = Pix2TextMFDDetector()
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


def test_mfd_apply_recognizes_existing_non_latex_formula_block(monkeypatch) -> None:
    detector = Pix2TextMFDDetector(max_existing_ocr_blocks=2)
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
