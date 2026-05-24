from pathlib import Path

from src.core.external_formula_tools import ExternalFormulaCandidate, ExternalFormulaToolSpec
from src.core.models import BlockType, DocumentBlock


def _case() -> object:
    return type(
        "Case",
        (),
        {
            "name": "fake",
            "pdf": Path("fake.pdf"),
            "latex_root": Path("fake-latex"),
        },
    )()


def _block(block_id: str = "p0_b1") -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$\alpha+\beta$$",
        bbox=(10.0, 20.0, 110.0, 50.0),
        metadata={"needs_ocr": True, "formula_score": 0.9},
    )


def test_formula_tool_comparison_persists_candidate_outputs(monkeypatch, tmp_path) -> None:
    from tools import formula_tool_comparison as tool

    class FakeRunner:
        def recognize_images(
            self,
            images: list[tuple[str, bytes]],
            specs: list[ExternalFormulaToolSpec] | None = None,
        ) -> list[ExternalFormulaCandidate]:
            assert images == [("p0_b1", b"png-bytes")]
            return [
                ExternalFormulaCandidate(
                    candidate_id="p0_b1",
                    latex=r"\alpha+\beta",
                    model="fake_formula",
                    model_version="v1",
                    preprocess_version="png-v1",
                    score=0.91,
                    duration_ms=17,
                    warnings=("candidate_only",),
                )
            ]

    monkeypatch.setattr(
        tool,
        "_collect_formula_blocks",
        lambda pdf, start_page, max_pages, sample_limit: (1, [_block()], 1),
    )
    monkeypatch.setattr(
        tool,
        "_crop_formula_samples",
        lambda pdf, blocks, dpi: (
            [
                tool.FormulaToolSample(
                    candidate_id="p0_b1",
                    page_num=0,
                    bbox=(10.0, 20.0, 110.0, 50.0),
                    source_text=r"$$\alpha+\beta$$",
                    input_hash="hash-1",
                    image_bytes=9,
                )
            ],
            [("p0_b1", b"png-bytes")],
            0.01,
        ),
    )
    monkeypatch.setattr(tool, "compute_sha256", lambda path: "doc-hash-abcdef")

    report = tool.compare_case(
        _case(),
        db_path=tmp_path / "formula_jobs.db",
        specs=[
            ExternalFormulaToolSpec(
                name="fake_formula",
                backend="fake_formula",
                python="python",
            )
        ],
        runner=FakeRunner(),
        source_formulas=[r"\alpha+\beta"],
    )

    assert report.status == "ok"
    assert report.sampled_blocks == 1
    assert report.candidates[0].model == "fake_formula"
    assert report.candidates[0].source_similarity >= 0.98
    assert report.summary[0].nonempty == 1
    assert report.round_jobs == {"r2_local_high_precision:done": 1}

    store = tool.FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    results = store.list_recognition_results(
        "doc-hash-abcdef",
        candidate_id="p0_b1",
        stage="local_precise",
    )
    assert len(results) == 1
    assert results[0].latex == r"\alpha+\beta"
    assert results[0].accepted is False
    assert results[0].warnings == ("candidate_only",)


def test_formula_tool_comparison_skips_when_no_tools(monkeypatch, tmp_path) -> None:
    from tools import formula_tool_comparison as tool

    monkeypatch.setattr(
        tool,
        "_collect_formula_blocks",
        lambda pdf, start_page, max_pages, sample_limit: (1, [_block()], 1),
    )
    monkeypatch.setattr(
        tool,
        "_crop_formula_samples",
        lambda pdf, blocks, dpi: (
            [
                tool.FormulaToolSample(
                    candidate_id="p0_b1",
                    page_num=0,
                    bbox=(10.0, 20.0, 110.0, 50.0),
                    source_text=r"$$\alpha+\beta$$",
                    input_hash="hash-1",
                    image_bytes=9,
                )
            ],
            [("p0_b1", b"png-bytes")],
            0.01,
        ),
    )
    monkeypatch.setattr(tool, "compute_sha256", lambda path: "doc-hash-abcdef")

    report = tool.compare_case(
        _case(),
        db_path=tmp_path / "formula_jobs.db",
        specs=[],
        source_formulas=[r"\alpha+\beta"],
    )

    assert report.status == "no_tools_configured"
    assert report.candidates == []
    assert report.round_jobs == {"r2_local_high_precision:skipped": 1}


def test_formula_tool_comparison_marks_empty_tool_results_failed(monkeypatch, tmp_path) -> None:
    from tools import formula_tool_comparison as tool

    class FakeRunner:
        def recognize_images(
            self,
            images: list[tuple[str, bytes]],
            specs: list[ExternalFormulaToolSpec] | None = None,
        ) -> list[ExternalFormulaCandidate]:
            return [
                ExternalFormulaCandidate(
                    candidate_id="p0_b1",
                    latex="",
                    model="fake_formula",
                    model_version="v1",
                    preprocess_version="png-v1",
                    warnings=("tool_failed:boom",),
                )
            ]

    monkeypatch.setattr(
        tool,
        "_collect_formula_blocks",
        lambda pdf, start_page, max_pages, sample_limit: (1, [_block()], 1),
    )
    monkeypatch.setattr(
        tool,
        "_crop_formula_samples",
        lambda pdf, blocks, dpi: (
            [
                tool.FormulaToolSample(
                    candidate_id="p0_b1",
                    page_num=0,
                    bbox=(10.0, 20.0, 110.0, 50.0),
                    source_text=r"$$\alpha+\beta$$",
                    input_hash="hash-1",
                    image_bytes=9,
                )
            ],
            [("p0_b1", b"png-bytes")],
            0.01,
        ),
    )
    monkeypatch.setattr(tool, "compute_sha256", lambda path: "doc-hash-abcdef")

    report = tool.compare_case(
        _case(),
        db_path=tmp_path / "formula_jobs.db",
        specs=[
            ExternalFormulaToolSpec(
                name="fake_formula",
                backend="fake_formula",
                python="python",
            )
        ],
        runner=FakeRunner(),
        source_formulas=[r"\alpha+\beta"],
    )

    assert report.status == "ok"
    assert report.summary[0].failed == 1
    assert report.round_jobs == {"r2_local_high_precision:failed": 1}
