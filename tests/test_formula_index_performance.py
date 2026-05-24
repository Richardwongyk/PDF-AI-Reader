from pathlib import Path

from src.core.models import BlockType, DocumentBlock


def test_formula_index_performance_records_import_rounds(monkeypatch, tmp_path) -> None:
    from tools import formula_index_performance as perf

    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf")})()

    class FakeDoc:
        page_count = 2

        def __getitem__(self, page_num: int) -> int:
            return page_num

        def close(self) -> None:
            return None

    class FakeChunker:
        def chunk_page(self, doc: object, page_num: int) -> list[DocumentBlock]:
            return [
                DocumentBlock(
                    id=f"p{page_num}_b0",
                    page_num=page_num,
                    block_type=BlockType.PARAGRAPH,
                    content="paragraph",
                    bbox=(0, 0, 10, 10),
                ),
                DocumentBlock(
                    id=f"p{page_num}_b1",
                    page_num=page_num,
                    block_type=BlockType.FORMULA,
                    content="[image formula]",
                    bbox=(10, 10, 40, 20),
                    metadata={"needs_ocr": True, "formula_score": 0.8},
                ),
            ]

    monkeypatch.setattr(perf.fitz, "open", lambda pdf: FakeDoc())
    monkeypatch.setattr(perf, "DocumentChunker", lambda **kwargs: FakeChunker())
    monkeypatch.setattr(perf, "compute_sha256", lambda path: "doc-hash-abcdef")

    report = perf.benchmark_case(
        case,
        db_path=tmp_path / "formula_jobs.db",
        max_pages=2,
    )

    assert report.pages_scanned == 2
    assert report.blocks == 4
    assert report.formula_blocks == 2
    assert report.pending_formula_blocks == 2
    assert report.page_jobs == {"queued": 2}
    assert report.formula_jobs == {"queued": 2}
    assert report.round_jobs == {
        "r0_pdf_structure:queued": 2,
        "r1_cached_recognition:queued": 2,
        "r3_cloud_semantic_review:queued": 2,
    }
    assert report.parse_ms_per_page >= 0
    assert report.persist_ms_per_page >= 0


def test_formula_index_performance_selects_bundled_cases() -> None:
    from tools.formula_index_performance import _select_cases

    cases = _select_cases("all")
    names = {case.name for case in cases}

    assert {"attention", "napkin"}.issubset(names)
