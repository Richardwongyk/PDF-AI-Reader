from pathlib import Path

from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.core.models import BlockType, DocumentBlock


def _case() -> object:
    return type("Case", (), {"name": "fake", "pdf": Path("fake.pdf")})()


def _blocks() -> list[DocumentBlock]:
    return [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.HEADING,
            content="Attention",
            bbox=(0, 0, 100, 20),
        ),
        DocumentBlock(
            id="p0_b1",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$\alpha+\beta$$",
            bbox=(10, 20, 110, 50),
            metadata={"needs_ocr": True, "formula_score": 0.9},
        ),
    ]


def test_multiround_pipeline_reports_r0_to_r4(monkeypatch, tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    monkeypatch.setattr(pipe, "_parse_blocks", lambda pdf, max_pages, start_page: (1, _blocks()))
    monkeypatch.setattr(pipe, "compute_sha256", lambda path: "doc-hash-abcdef")

    class FakePageWorker:
        def __init__(self, filepath, page_nums, blocks, doc_hash="", scan_round="") -> None:
            self.finished_signal = _Signal()
            self._doc_hash = doc_hash
            self._scan_round = scan_round

        def run(self) -> None:
            self.finished_signal.emit(
                {
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "done_pages": [0],
                    "failed": [],
                    "detected": [],
                    "structure_candidates": [
                        {
                            "candidate_id": "p0_r0_0",
                            "page_num": 0,
                            "bbox": (10, 20, 110, 50),
                            "text": r"\alpha+\beta",
                            "latex": r"\alpha+\beta",
                            "score": 0.4,
                            "input_hash": "glyph-hash",
                            "warnings": ["low_confidence"],
                            "evidence": {},
                        }
                    ],
                }
            )

    class FakeOcrWorker:
        def __init__(
            self,
            filepath,
            blocks,
            doc_hash="",
            cache_only=True,
            scan_round="",
            external_tool_specs=None,
        ) -> None:
            self.finished_signal = _Signal()
            self._blocks = blocks
            self._doc_hash = doc_hash
            self._scan_round = scan_round
            self._cache_only = cache_only

        def run(self) -> None:
            if self._cache_only:
                self.finished_signal.emit(
                    {
                        "doc_hash": self._doc_hash,
                        "scan_round": self._scan_round,
                        "updated": [],
                        "pending": 0,
                        "done": [],
                        "skipped": [
                            {
                                "block_id": block.id,
                                "reason": "cache_miss",
                                "scan_round": self._scan_round,
                            }
                            for block in self._blocks
                        ],
                        "failed": [],
                    }
                )
            else:
                self.finished_signal.emit(
                    {
                        "doc_hash": self._doc_hash,
                        "scan_round": self._scan_round,
                        "updated": [],
                        "pending": 0,
                        "done": [
                            {
                                "block_id": block.id,
                                "latex": r"\alpha+\beta",
                                "image_hash": "image-hash",
                                "model": "fake_tool",
                                "scan_round": self._scan_round,
                            }
                            for block in self._blocks
                        ],
                        "skipped": [],
                        "failed": [],
                    }
                )

    monkeypatch.setattr(pipe, "_FormulaPageScanWorker", FakePageWorker)
    monkeypatch.setattr(pipe, "_FormulaOcrWorker", FakeOcrWorker)

    report = pipe.run_pipeline_case(
        _case(),
        formula_db_path=tmp_path / "formula_jobs.db",
        graph_db_path=tmp_path / "graph_jobs.db",
        max_pages=1,
        r1_limit=1,
        r2_limit=1,
        r3_limit=1,
        r4_limit=2,
    )

    rounds = {item.round: item for item in report.rounds}
    assert set(rounds) == {
        FormulaScanRound.PDF_STRUCTURE.value,
        FormulaScanRound.SYMBOL_IDENTITY_REPAIR.value,
        FormulaScanRound.CACHED_RECOGNITION.value,
        FormulaScanRound.LOCAL_HIGH_PRECISION.value,
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
        FormulaScanRound.KNOWLEDGE_GRAPH.value,
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE.value,
    }
    assert report.formula_round_jobs["r0_pdf_structure:done"] >= 1
    assert report.formula_round_jobs["r2_local_high_precision:done"] == 1
    assert report.formula_round_jobs["r3_cloud_semantic_review:done"] == 1
    assert report.formula_round_jobs["r4_knowledge_graph:done"] == 1
    assert report.graph_jobs["done"] == 1
    assert report.recognition_results["pdf_structure:pymupdf_born_digital_structure"] == 1
    assert report.recognition_results["local_precise:fake_tool"] == 1
    assert report.formula_fusion_jobs
    assert [item["stage"] for item in report.formula_fusion_snapshots] == [
        "after_initial_r2",
        "after_r3",
    ]
    assert report.formula_fusion_snapshots[0]["persisted"]["fusion_records_upserted"] >= 1

    r4_details = rounds[FormulaScanRound.KNOWLEDGE_GRAPH.value].details
    assert r4_details["extractor"] == "structural_v1"
    assert r4_details["graph_jobs"] == {"done": 1}


def test_multiround_pipeline_reuses_done_jobs(monkeypatch, tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    monkeypatch.setattr(pipe, "_parse_blocks", lambda pdf, max_pages, start_page: (1, _blocks()))
    monkeypatch.setattr(pipe, "compute_sha256", lambda path: "doc-hash-abcdef")

    calls = {"r0": 0, "r2": 0}

    class FakePageWorker:
        def __init__(self, filepath, page_nums, blocks, doc_hash="", scan_round="") -> None:
            self.finished_signal = _Signal()
            self._doc_hash = doc_hash
            self._scan_round = scan_round

        def run(self) -> None:
            calls["r0"] += 1
            self.finished_signal.emit(
                {
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "done_pages": [0],
                    "failed": [],
                    "detected": [],
                    "structure_candidates": [],
                }
            )

    class FakeOcrWorker:
        def __init__(
            self,
            filepath,
            blocks,
            doc_hash="",
            cache_only=True,
            scan_round="",
            external_tool_specs=None,
        ) -> None:
            self.finished_signal = _Signal()
            self._blocks = blocks
            self._doc_hash = doc_hash
            self._scan_round = scan_round
            self._cache_only = cache_only

        def run(self) -> None:
            if not self._cache_only:
                calls["r2"] += 1
            self.finished_signal.emit(
                {
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "updated": [],
                    "pending": 0,
                    "done": [
                        {
                            "block_id": block.id,
                            "latex": r"\alpha+\beta",
                            "image_hash": "image-hash",
                            "model": "fake_tool",
                            "scan_round": self._scan_round,
                        }
                        for block in self._blocks
                    ],
                    "skipped": [],
                    "failed": [],
                }
            )

    monkeypatch.setattr(pipe, "_FormulaPageScanWorker", FakePageWorker)
    monkeypatch.setattr(pipe, "_FormulaOcrWorker", FakeOcrWorker)

    kwargs = {
        "formula_db_path": tmp_path / "formula_jobs.db",
        "graph_db_path": tmp_path / "graph_jobs.db",
        "max_pages": 1,
        "r1_limit": 1,
        "r2_limit": 1,
        "r2_sample_formulas": 1,
        "r3_limit": 1,
        "r4_limit": 2,
    }
    first = pipe.run_pipeline_case(_case(), **kwargs)
    second = pipe.run_pipeline_case(_case(), **kwargs)

    first_rounds = {item.round: item for item in first.rounds}
    second_rounds = {item.round: item for item in second.rounds}
    assert calls == {"r0": 1, "r2": 1}
    assert first_rounds[FormulaScanRound.LOCAL_HIGH_PRECISION.value].status == "done"
    assert second_rounds[FormulaScanRound.PDF_STRUCTURE.value].details["skipped_completed_pages"] == 1
    assert second_rounds[FormulaScanRound.LOCAL_HIGH_PRECISION.value].details["reason"] == "no_pending_r2_blocks"


def test_multiround_pipeline_can_run_targeted_r2_after_fusion(monkeypatch, tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    monkeypatch.setattr(pipe, "_parse_blocks", lambda pdf, max_pages, start_page: (1, _blocks()))
    monkeypatch.setattr(pipe, "compute_sha256", lambda path: "doc-hash-targeted")

    class FakePageWorker:
        def __init__(self, filepath, page_nums, blocks, doc_hash="", scan_round="") -> None:
            self.finished_signal = _Signal()
            self._doc_hash = doc_hash
            self._scan_round = scan_round

        def run(self) -> None:
            self.finished_signal.emit(
                {
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "done_pages": [0],
                    "failed": [],
                    "detected": [],
                    "structure_candidates": [
                        {
                            "candidate_id": "p0_b1",
                            "page_num": 0,
                            "bbox": (10, 20, 110, 50),
                            "text": "wrong",
                            "latex": "wrong",
                            "score": 0.2,
                            "input_hash": "glyph-hash",
                            "warnings": ["low_confidence"],
                            "evidence": {},
                        }
                    ],
                }
            )

    class FakeOcrWorker:
        full_runs = 0

        def __init__(
            self,
            filepath,
            blocks,
            doc_hash="",
            cache_only=True,
            scan_round="",
            external_tool_specs=None,
        ) -> None:
            self.finished_signal = _Signal()
            self._blocks = blocks
            self._doc_hash = doc_hash
            self._scan_round = scan_round
            self._cache_only = cache_only

        def run(self) -> None:
            if self._cache_only:
                self.finished_signal.emit({
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "updated": [],
                    "pending": 0,
                    "done": [],
                    "skipped": [],
                    "failed": [],
                })
                return
            FakeOcrWorker.full_runs += 1
            self.finished_signal.emit(
                {
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "updated": [],
                    "pending": 0,
                    "done": [
                        {
                            "block_id": block.id,
                            "latex": r"\alpha+\beta",
                            "image_hash": "image-hash",
                            "model": "fake_tool",
                            "scan_round": self._scan_round,
                        }
                        for block in self._blocks
                    ],
                    "skipped": [],
                    "failed": [],
                }
            )

    monkeypatch.setattr(pipe, "_FormulaPageScanWorker", FakePageWorker)
    monkeypatch.setattr(pipe, "_FormulaOcrWorker", FakeOcrWorker)

    report = pipe.run_pipeline_case(
        _case(),
        formula_db_path=tmp_path / "formula_jobs.db",
        graph_db_path=tmp_path / "graph_jobs.db",
        max_pages=1,
        r1_limit=0,
        r2_limit=1,
        r3_limit=0,
        r4_limit=1,
        run_targeted_r2_after_fusion=True,
    )

    assert FakeOcrWorker.full_runs == 1
    r2_rounds = [
        item for item in report.rounds
        if item.round == FormulaScanRound.LOCAL_HIGH_PRECISION.value
    ]
    assert len(r2_rounds) == 2
    assert r2_rounds[-1].status == "done"
    assert report.recognition_results["local_precise:fake_tool"] == 1


def test_multiround_pipeline_can_drain_targeted_r2_batches(monkeypatch, tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    blocks = [
        DocumentBlock(
            id=f"p0_b{index}",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=f"$$wrong{index}$$",
            bbox=(10 + index * 20, 20, 20 + index * 20, 40),
            metadata={"needs_ocr": True, "formula_score": 0.1},
        )
        for index in range(2)
    ]
    monkeypatch.setattr(pipe, "_parse_blocks", lambda pdf, max_pages, start_page: (1, blocks))
    monkeypatch.setattr(pipe, "compute_sha256", lambda path: "doc-hash-r2-drain")

    class FakePageWorker:
        def __init__(self, filepath, page_nums, blocks, doc_hash="", scan_round="") -> None:
            self.finished_signal = _Signal()
            self._doc_hash = doc_hash
            self._scan_round = scan_round

        def run(self) -> None:
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "done_pages": [0],
                "failed": [],
                "detected": [],
                "structure_candidates": [],
            })

    class FakeOcrWorker:
        full_runs = 0

        def __init__(self, filepath, blocks, doc_hash="", cache_only=True, scan_round="", external_tool_specs=None) -> None:
            self.finished_signal = _Signal()
            self._blocks = blocks
            self._doc_hash = doc_hash
            self._scan_round = scan_round
            self._cache_only = cache_only

        def run(self) -> None:
            if self._cache_only:
                self.finished_signal.emit({
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "updated": [],
                    "pending": 0,
                    "done": [],
                    "skipped": [],
                    "failed": [],
                })
                return
            FakeOcrWorker.full_runs += 1
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "updated": [],
                "pending": 0,
                "done": [
                    {
                        "block_id": block.id,
                        "latex": rf"\alpha_{block.id}",
                        "image_hash": f"image-{block.id}",
                        "model": "fake_tool",
                        "scan_round": self._scan_round,
                    }
                    for block in self._blocks
                ],
                "skipped": [],
                "failed": [],
            })

    monkeypatch.setattr(pipe, "_FormulaPageScanWorker", FakePageWorker)
    monkeypatch.setattr(pipe, "_FormulaOcrWorker", FakeOcrWorker)

    report = pipe.run_pipeline_case(
        _case(),
        formula_db_path=tmp_path / "formula_jobs.db",
        graph_db_path=tmp_path / "graph_jobs.db",
        max_pages=1,
        r1_limit=0,
        r2_limit=1,
        r2_sample_formulas=2,
        r3_limit=0,
        r4_limit=1,
        drain_r2=True,
    )

    r2_round = next(item for item in report.rounds if item.round == FormulaScanRound.LOCAL_HIGH_PRECISION.value)
    assert FakeOcrWorker.full_runs == 2
    assert r2_round.details["batches"] == 2
    assert r2_round.details["drained"] is True
    assert report.formula_round_jobs["r2_local_high_precision:done"] == 2


def test_formula_accuracy_report_compares_each_round_to_source(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe
    from src.app.formula_index_store import FormulaIndexStore

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(
        r"""
        \begin{document}
        $$\alpha+\beta$$
        $$\frac{a}{b}$$
        \end{document}
        """,
        encoding="utf-8",
    )
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = DocumentBlock(
        id="p0_b1",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$\alpha+\beta$$",
        bbox=(0, 0, 10, 10),
    )
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="pdf_structure",
        model="pymupdf_born_digital_structure",
        model_version="v1",
        preprocess_version="glyph",
        input_hash="glyph-hash",
        latex=r"\alpha+\beta",
        normalized_latex=r"\alpha+\beta",
    )
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="local_precise",
        model="fake_tool",
        model_version="v1",
        preprocess_version="png",
        input_hash="image-hash",
        latex=r"\frac{a}{b}",
        normalized_latex=r"\frac{a}{b}",
    )
    store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        fusion_version=pipe.FUSION_VERSION,
        input_hash="fusion-hash",
        best_result_id="r2-result",
        ranked_result_ids=["r2-result"],
        coverage=1.0,
        agreement_score=1.0,
        source_similarity=1.0,
        syntax_valid=True,
        risk_flags=[],
        accepted_gate={"passed": True, "reasons": []},
        decision="ready_for_manual_accept",
        result_json={
            "best_latex": r"\frac{a}{b}",
            "ranked_candidates": [
                {
                    "latex": r"\frac{a}{b}",
                    "accepted": True,
                }
            ],
        },
    )

    report = pipe._formula_accuracy_report(case, store, "doc-1", [block], [block], max_pages=0)

    by_group = {item["group"]: item for item in report["stage_metrics"]}
    assert report["available"] is True
    assert by_group["parsed_blocks:document_chunker"]["near_match_rate"] >= 0.99
    assert by_group["pdf_structure:pymupdf_born_digital_structure"]["near_match_rate"] >= 0.99
    assert by_group["local_precise:fake_tool"]["near_match_rate"] >= 0.99
    assert by_group[f"fusion_best:{pipe.FUSION_VERSION}"]["near_match_rate"] >= 0.99
    assert by_group[f"fusion_accepted:{pipe.FUSION_VERSION}"]["near_match_rate"] >= 0.99
    assert report["monotonic"]["checked"] is True


def test_formula_accuracy_report_counts_inline_math_candidates(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe
    from src.app.formula_index_store import FormulaIndexStore

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"inline \(x_i\)", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    paragraph = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content=r"inline \(x_i\)",
        bbox=(0, 0, 100, 20),
    )

    report = pipe._formula_accuracy_report(case, store, "doc-1", [paragraph], [], max_pages=0)

    by_group = {item["group"]: item for item in report["stage_metrics"]}
    assert by_group["inline_spans:document_chunker"]["candidate_count"] == 1
    assert by_group["inline_spans:document_chunker"]["inline_near_match_rate"] >= 0.99


def test_formula_fusion_report_includes_inline_math_candidates(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe
    from src.app.formula_index_store import FormulaIndexStore

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"inline \(x_i\)", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    paragraph = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content=r"inline \(x_i\)",
        bbox=(0, 0, 100, 20),
        metadata={
            "inline_math_candidates": [
                {
                    "latex": "x_i",
                    "wrapped_latex": r"\(x_i\)",
                    "source": "pdf_math_font_spans",
                    "bbox": [10, 2, 22, 12],
                    "fonts": ["CMMI10", "CMMI7"],
                    "span_count": 2,
                    "has_script_size": True,
                    "font_size_min": 7.0,
                    "font_size_max": 10.0,
                    "spans": [
                        {"text": "x", "font": "CMMI10", "size": 10.0, "bbox": [10, 2, 17, 12]},
                        {"text": "_i", "font": "CMMI7", "size": 7.0, "bbox": [17, 6, 22, 13]},
                    ],
                }
            ]
        },
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", [paragraph], [], max_pages=0)

    rows = {row["candidate_id"]: row for row in report["candidate_rows"]}
    assert "p0_b0_inline_0" in rows
    assert rows["p0_b0_inline_0"]["best_stage"] == "inline_spans"
    inline_candidate = rows["p0_b0_inline_0"]["ranked_candidates"][0]
    assert inline_candidate["evidence"]["source_context"] == r"inline \(x_i\)"
    assert inline_candidate["evidence"]["inline_pdf_evidence"]["has_script_size"] is True
    assert inline_candidate["evidence"]["bbox"] == [10, 2, 22, 12]
    assert not any(item["candidate_id"] == "p0_b0_inline_0" for item in report["targeted_r2_queue"])


def test_formula_fusion_keeps_multiple_inline_formulas_in_same_paragraph_separate(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"inline \(x_i\) and \(y_j\)", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    paragraph = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content=r"inline \(x_i\) and \(y_j\)",
        bbox=(0, 0, 100, 20),
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", [paragraph], [], max_pages=0)
    rows = {row["candidate_id"]: row for row in report["candidate_rows"]}

    assert set(rows) == {"p0_b0_inline_0", "p0_b0_inline_1"}
    assert all(row["candidate_count"] == 1 for row in rows.values())


def test_formula_fusion_prioritizes_high_value_cloud_review_without_dropping_inline(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(
        r"inline \(t\) and \(\sum_{i=1}^{n} x_i\)",
        encoding="utf-8",
    )
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    paragraph = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content=r"inline \(t\) and \(\sum_{i=1}^{n} x_i\)",
        bbox=(0, 0, 120, 20),
    )

    pipe._formula_fusion_report(case, store, "doc-1", [paragraph], [], max_pages=0, filepath="paper.pdf")

    queued = store.list_round_records(
        "doc-1",
        statuses={"queued"},
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        limit=10,
    )
    assert {record.target_id for record in queued} == {"p0_b0_inline_0", "p0_b0_inline_1"}
    assert queued[0].target_id == "p0_b0_inline_1"
    assert queued[0].priority > queued[1].priority
    assert queued[0].result_json["review_candidate"]["latex"] == r"\sum_{i=1}^{n} x_i"
    assert "low_value_inline=True" in queued[1].result_json["review_priority_reason"]
    assert "low_value_inline=False" in queued[0].result_json["review_priority_reason"]


def test_pipeline_r4_writes_fusion_candidate_graph_nodes(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe
    from src.app.graph_index_store import GraphIndexStore

    formula_store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    fusion = {
        "candidate_rows": [
            {
                "candidate_id": "p0_b0_inline_0",
                "best_latex": "x_i",
                "best_similarity": 0.8,
                "best_result_id": "inline-result",
                "fusion_input_hash": "fusion-hash",
                "decision": "needs_more_evidence",
                "accepted_gate": {"passed": False},
                "ranked_candidates": [
                    {
                        "latex": "x_i",
                        "evidence": {"page_num": 0, "bbox": [1, 2, 3, 4]},
                    }
                ],
            }
        ]
    }

    report = pipe._run_r4(
        formula_store,
        graph_store,
        "paper.pdf",
        "doc-1",
        [],
        formula_fusion=fusion,
        limit=1,
    )

    assert report.status == "done"
    assert report.counts == {"r4_knowledge_graph:done": 1}
    assert report.details["queued_formula_candidates"] == 1
    artifact = graph_store.artifacts("doc-1", "p0_b0_inline_0")[0]
    assert any(node.get("type") == "formula_candidate" for node in artifact["nodes"])


def test_multiround_pipeline_can_drain_r3_and_r4_batches(monkeypatch, tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    paragraph_blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.PARAGRAPH,
            content=r"inline \(x_i\) and \(y_j\)",
            bbox=(0, 0, 100, 20),
        ),
    ]
    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"inline \(x_i\) and \(y_j\)", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()

    monkeypatch.setattr(pipe, "_parse_blocks", lambda pdf, max_pages, start_page: (1, paragraph_blocks))
    monkeypatch.setattr(pipe, "compute_sha256", lambda path: "doc-drain")

    class FakePageWorker:
        def __init__(self, filepath, page_nums, blocks, doc_hash="", scan_round="") -> None:
            self.finished_signal = _Signal()
            self._doc_hash = doc_hash
            self._scan_round = scan_round

        def run(self) -> None:
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "done_pages": [0],
                "failed": [],
                "detected": [],
                "structure_candidates": [],
            })

    monkeypatch.setattr(pipe, "_FormulaPageScanWorker", FakePageWorker)

    report = pipe.run_pipeline_case(
        case,
        formula_db_path=tmp_path / "formula_jobs.db",
        graph_db_path=tmp_path / "graph_jobs.db",
        max_pages=1,
        r1_limit=0,
        r2_limit=0,
        r3_limit=1,
        r4_limit=1,
        drain_r3=True,
        drain_r4=True,
    )

    rounds = {item.round: item for item in report.rounds}
    assert rounds[FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value].details["batches"] == 2
    assert rounds[FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value].details["drained"] is True
    assert rounds[FormulaScanRound.KNOWLEDGE_GRAPH.value].details["batches"] == 2
    assert rounds[FormulaScanRound.KNOWLEDGE_GRAPH.value].details["drained"] is True
    assert report.formula_round_jobs["r3_cloud_semantic_review:done"] == 2
    assert report.formula_round_jobs["r4_knowledge_graph:done"] == 2


def test_formula_fusion_report_ranks_candidates_and_targets_low_similarity(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(
        r"""
        \begin{document}
        $$\alpha+\beta$$
        $$\frac{a}{b}$$
        \end{document}
        """,
        encoding="utf-8",
    )
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    blocks = [
        DocumentBlock(
            id="good",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$\frac{a}{b}$$",
            bbox=(0, 0, 10, 10),
        ),
        DocumentBlock(
            id="needs_r2",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$unrelated words$$",
            bbox=(0, 20, 10, 30),
        ),
    ]
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="good",
        stage="local_precise",
        model="fake_tool",
        model_version="v1",
        preprocess_version="png",
        input_hash="image-hash",
        latex=r"\frac{a}{b}",
        normalized_latex=r"\frac{a}{b}",
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", blocks, blocks, max_pages=0, filepath="paper.pdf")

    rows = {row["candidate_id"]: row for row in report["candidate_rows"]}
    assert rows["good"]["best_stage"] == "local_precise"
    assert rows["good"]["has_local_precise"] is True
    assert rows["good"]["best_similarity"] >= 0.9
    assert rows["needs_r2"]["decision"] == "needs_more_evidence"
    assert report["targeted_r2_queue"][0]["candidate_id"] == "needs_r2"
    assert store.fusion_counts("doc-1")["needs_more_evidence"] == 1
    assert store.fusion_counts("doc-1")["ready_for_manual_accept"] == 1
    assert store.round_counts("doc-1", FormulaScanRound.LOCAL_HIGH_PRECISION) == {
        "r2_local_high_precision:queued": 1,
    }


def test_formula_fusion_rejects_degraded_local_precise_candidate(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"$$\alpha+\beta$$", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$\alpha+\beta$$",
        bbox=(0, 0, 10, 10),
    )
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b0",
        stage="pdf_structure",
        model="pymupdf_born_digital_structure",
        model_version="v1",
        preprocess_version="glyph",
        input_hash="glyph-hash",
        latex=r"\alpha+\beta",
        normalized_latex=r"\alpha+\beta",
        evidence={"page_num": 0, "bbox": [0, 0, 10, 10]},
    )
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b0",
        stage="local_precise",
        model="slow_mfr",
        model_version="v1",
        preprocess_version="png",
        input_hash="image-hash",
        latex=r"\alpha-\beta",
        normalized_latex=r"\alpha-\beta",
        evidence={"page_num": 0, "bbox": [0, 0, 10, 10]},
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", [block], [block], max_pages=0, filepath="paper.pdf")

    row = report["candidate_rows"][0]
    assert row["best_stage"] in {"parsed_blocks", "pdf_structure"}
    assert row["decision"] == "needs_more_evidence"
    assert row["accepted_gate"]["passed"] is False
    assert "local_precise_degraded_against_born_digital" in row["risk_flags"]
    assert "local_precise_degraded_against_born_digital" in row["accepted_gate"]["reasons"]
    assert report["summary"]["ready_for_manual_accept"] == 0
    assert report["summary"]["local_precise_degraded"] == 1


def test_formula_fusion_report_includes_cloud_semantic_suggestion(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"$$\alpha+\beta$$", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$\alpha+\beta$$",
        bbox=(0, 0, 10, 10),
    )
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
        result_json_by_target={
            "p0_b0": {
                "input_hash": "r3-input",
                "suggested_latex": r"\alpha+\beta",
                "confidence": "0.98",
                "model": "deepseek-test",
                "risks": [],
                "reason": "same expression",
            }
        },
    )
    store.mark_round_done(
        "doc-1",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        "p0_b0",
        {
            "input_hash": "r3-input",
            "suggested_latex": r"\alpha+\beta",
            "confidence": "0.98",
            "model": "deepseek-test",
            "risks": [],
            "reason": "same expression",
        },
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", [block], [block], max_pages=0, filepath="paper.pdf")

    row = report["candidate_rows"][0]
    assert "cloud_semantic" in row["stages"]
    assert row["stage_quality"]["cloud_semantic"]["best_model"] == "suggested_latex"
    cloud = [item for item in row["ranked_candidates"] if item["stage"] == "cloud_semantic"][0]
    assert cloud["score"] == 0.98


def test_formula_fusion_report_persists_and_skips_same_input(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"$$\alpha+\beta$$", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    blocks = [
        DocumentBlock(
            id="needs_r2",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$unrelated words$$",
            bbox=(0, 20, 10, 30),
        ),
    ]

    first = pipe._formula_fusion_report(
        case,
        store,
        "doc-1",
        blocks,
        blocks,
        max_pages=0,
        filepath="paper.pdf",
    )
    second = pipe._formula_fusion_report(
        case,
        store,
        "doc-1",
        blocks,
        blocks,
        max_pages=0,
        filepath="paper.pdf",
    )

    first_persisted = first["summary"]["persisted"]
    second_persisted = second["summary"]["persisted"]
    assert first_persisted["fusion_records_upserted"] == 1
    assert first_persisted["r2_queued"] == 1
    assert second_persisted["already_done_same_input"] == 1
    assert second_persisted["fusion_records_upserted"] == 0
    assert second_persisted["r2_queued"] == 0
    assert len(store.list_fusion_records("doc-1")) == 1


def test_formula_fusion_queues_r5_only_for_accepted_results(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe

    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = DocumentBlock(
        id="accepted",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$old$$",
        bbox=(0, 0, 10, 10),
    )
    row = pipe._fusion_row(
        "accepted",
        [
            pipe._fusion_candidate(
                candidate_id="accepted",
                result_id="r2-result",
                stage="local_precise",
                model="fake_tool",
                model_version="v1",
                preprocess_version="png",
                input_hash="image-hash",
                latex=r"\alpha+\beta",
                source_formulas=[],
                score=0.99,
                warnings=[],
                accepted=True,
                evidence={"page_num": 0, "bbox": [0, 0, 10, 10]},
            )
        ],
    )

    persisted = pipe._persist_fusion_rows(
        store,
        "doc-1",
        "paper.pdf",
        [row],
        [block],
    )

    assert persisted["r5_queued"] == 1
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert len(records) == 1
    assert records[0].result_json["input_hash"] == row["fusion_input_hash"]


def test_formula_fusion_report_merges_same_bbox_candidates(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe
    from src.app.formula_index_store import FormulaIndexStore

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(
        r"$$E=mc^2$$",
        encoding="utf-8",
    )
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$E=mc^2$$",
            bbox=(10.0, 20.0, 110.0, 50.0),
        ),
    ]
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_r0_0",
        stage="pdf_structure",
        model="pymupdf",
        model_version="v1",
        preprocess_version="glyph",
        input_hash="glyph-hash",
        latex=r"E=mc^2",
        normalized_latex=r"E=mc^2",
        evidence={"page_num": 0, "bbox": [10.0, 20.0, 110.0, 50.0]},
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", blocks, blocks, max_pages=0)

    assert report["summary"]["candidate_count"] == 1
    row = report["candidate_rows"][0]
    assert row["member_candidate_ids"] == ["p0_b0", "p0_r0_0"]
    assert row["stages"] == ["parsed_blocks", "pdf_structure"]


def test_formula_fusion_report_merges_later_candidate_id_members(tmp_path) -> None:
    from tools import formula_multiround_pipeline as pipe
    from src.app.formula_index_store import FormulaIndexStore

    latex_root = tmp_path / "latex"
    latex_root.mkdir()
    (latex_root / "main.tex").write_text(r"$$E=mc^2$$", encoding="utf-8")
    case = type("Case", (), {"name": "fake", "pdf": Path("fake.pdf"), "latex_root": latex_root})()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    blocks = [
        DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.FORMULA,
            content=r"$$E=mc^2$$",
            bbox=(10.0, 20.0, 110.0, 50.0),
        ),
    ]
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_r0_0",
        stage="pdf_structure",
        model="pymupdf",
        model_version="v1",
        preprocess_version="glyph",
        input_hash="glyph-hash",
        latex=r"E=mc^2",
        normalized_latex=r"E=mc^2",
        evidence={"page_num": 0, "bbox": [10.0, 20.0, 110.0, 50.0]},
    )
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_r0_0",
        stage="cloud_semantic",
        model="deepseek",
        model_version="v1",
        preprocess_version="prompt",
        input_hash="review-hash",
        latex=r"E=mc^2",
        normalized_latex=r"E=mc^2",
        score=0.95,
        evidence={"reason": "same stored candidate id"},
    )

    report = pipe._formula_fusion_report(case, store, "doc-1", blocks, blocks, max_pages=0)

    assert report["summary"]["candidate_count"] == 1
    row = report["candidate_rows"][0]
    assert row["member_candidate_ids"] == ["p0_b0", "p0_r0_0"]
    assert row["stages"] == ["cloud_semantic", "parsed_blocks", "pdf_structure"]


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self._callbacks.append(callback)

    def emit(self, *args: object) -> None:
        for callback in list(self._callbacks):
            callback(*args)  # type: ignore[misc]
