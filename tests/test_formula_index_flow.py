from src.app.formula_index_flow import FormulaIndexFlow, _FormulaPageScanWorker
from src.app.formula_index_scheduler import FormulaScanPlan
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.core.models import BlockType, DocumentBlock


def _formula(
    block_id: str,
    page_num: int,
    score: float = 0.5,
    needs_ocr: bool = True,
) -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=page_num,
        block_type=BlockType.FORMULA,
        content="[图片公式，等待 OCR 识别]",
        bbox=(0, 0, 100, 20),
        metadata={"needs_ocr": needs_ocr, "formula_score": score},
    )


def test_formula_index_flow_prioritizes_pages_and_filters_candidates() -> None:
    flow = FormulaIndexFlow()
    started: list[tuple[str, int]] = []

    def fake_start(filepath: str, batch_budget: int) -> None:
        started.append((filepath, batch_budget))

    flow._start_next_batch = fake_start  # type: ignore[method-assign]

    flow.enqueue_blocks(
        "paper.pdf",
        [
            _formula("late", 10, 0.99),
            _formula("early", 1, 0.20),
            _formula("done", 0, 0.99, needs_ocr=False),
            DocumentBlock(
                id="paragraph",
                page_num=0,
                block_type=BlockType.PARAGRAPH,
                content="not a formula",
                bbox=(0, 0, 10, 10),
            ),
        ],
        priority_pages={10},
        batch_budget=2,
    )

    assert started == [("paper.pdf", 2)]
    assert [block.id for block in flow._queued_blocks] == ["late", "early"]


def test_formula_index_flow_deduplicates_pending_queue() -> None:
    flow = FormulaIndexFlow()
    flow._start_next_batch = lambda filepath, batch_budget: None  # type: ignore[method-assign]

    flow.enqueue_blocks("paper.pdf", [_formula("p0_b1", 0)], batch_budget=1)
    flow.enqueue_blocks("paper.pdf", [_formula("p0_b1", 0), _formula("p0_b2", 0)], batch_budget=1)

    assert [block.id for block in flow._queued_blocks] == ["p0_b1", "p0_b2"]


def test_formula_index_flow_does_not_drain_queue_by_default() -> None:
    flow = FormulaIndexFlow()
    starts: list[int] = []

    def fake_start(filepath: str, batch_budget: int) -> None:
        starts.append(batch_budget)

    flow._start_next_batch = fake_start  # type: ignore[method-assign]
    flow._queued_blocks = [_formula("p1_b1", 1)]
    flow._drain_queue = False

    flow._on_worker_thread_done("paper.pdf", 8)

    assert starts == []


def test_formula_index_flow_can_drain_queue_when_enabled() -> None:
    flow = FormulaIndexFlow()
    starts: list[int] = []

    def fake_start(filepath: str, batch_budget: int) -> None:
        starts.append(batch_budget)

    flow._start_next_batch = fake_start  # type: ignore[method-assign]
    flow._queued_blocks = [_formula("p1_b1", 1)]
    flow._drain_queue = True

    flow._on_worker_thread_done("paper.pdf", 8)

    assert starts == [8]


def test_formula_index_flow_default_worker_is_cache_only() -> None:
    flow = FormulaIndexFlow()
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            filepath: str,
            blocks: list[DocumentBlock],
            doc_hash: str = "",
            cache_only: bool = True,
            scan_round: str = "",
        ) -> None:
            self.filepath = filepath
            self.blocks = blocks
            self.doc_hash = doc_hash
            self.cache_only = cache_only
            self.scan_round = scan_round
            self.finished_signal = _Signal()
            self.finished = _Signal()

        def start(self) -> None:
            workers.append(self)

        def isRunning(self) -> bool:
            return False

        def deleteLater(self) -> None:
            pass

    import src.app.formula_index_flow as module

    original_worker = module._FormulaOcrWorker
    module._FormulaOcrWorker = FakeWorker  # type: ignore[assignment]
    try:
        flow.enqueue_blocks("paper.pdf", [_formula("p0_b1", 0)], batch_budget=1)
    finally:
        module._FormulaOcrWorker = original_worker  # type: ignore[assignment]

    assert len(workers) == 1
    assert workers[0].cache_only is True
    assert workers[0].scan_round == FormulaScanRound.CACHED_RECOGNITION.value


def test_formula_index_flow_explicit_full_scan_can_load_model() -> None:
    flow = FormulaIndexFlow()
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            filepath: str,
            blocks: list[DocumentBlock],
            doc_hash: str = "",
            cache_only: bool = True,
            scan_round: str = "",
        ) -> None:
            self.doc_hash = doc_hash
            self.cache_only = cache_only
            self.scan_round = scan_round
            self.finished_signal = _Signal()
            self.finished = _Signal()

        def start(self) -> None:
            workers.append(self)

        def isRunning(self) -> bool:
            return False

        def deleteLater(self) -> None:
            pass

    import src.app.formula_index_flow as module

    original_worker = module._FormulaOcrWorker
    module._FormulaOcrWorker = FakeWorker  # type: ignore[assignment]
    try:
        flow.enqueue_blocks(
            "paper.pdf",
            [_formula("p0_b1", 0)],
            batch_budget=1,
            cache_only=False,
        )
    finally:
        module._FormulaOcrWorker = original_worker  # type: ignore[assignment]

    assert len(workers) == 1
    assert workers[0].cache_only is False
    assert workers[0].scan_round == FormulaScanRound.CACHED_RECOGNITION.value


def test_formula_index_flow_can_persist_plan_without_starting_worker(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    started: list[tuple[str, int]] = []
    flow._start_next_batch = lambda filepath, batch_budget: started.append((filepath, batch_budget))  # type: ignore[method-assign]
    plan = FormulaScanPlan(
        blocks=[_formula("p0_b1", 0), _formula("done", 0, needs_ocr=False)],
        priority_pages={0},
        batch_budget=8,
        drain_queue=False,
        cache_only=True,
        scan_round=FormulaScanRound.CACHED_RECOGNITION.value,
    )

    queued = flow.persist_plan("paper.pdf", "doc-1", plan)

    assert queued == 1
    assert started == []
    assert flow._queued_blocks == []
    assert store.counts("doc-1") == {"queued": 1}
    assert store.round_counts("doc-1") == {
        "r1_cached_recognition:queued": 1,
    }


def test_formula_index_flow_does_not_queue_done_formula_job(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula("p0_b1", 0)
    store.enqueue_blocks("doc-1", "paper.pdf", [block])
    store.mark_done("doc-1", "p0_b1", r"\alpha", "hash-1")
    flow = FormulaIndexFlow(store=store)

    flow.enqueue_blocks(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        batch_budget=0,
    )

    assert flow._queued_blocks == []
    assert store.counts("doc-1") == {"done": 1}


def test_formula_index_store_persists_page_scan_status(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))

    inserted = store.enqueue_pages("doc-1", "paper.pdf", [2, 0, 2], priority_pages={2})
    store.mark_pages_running("doc-1", [2])
    store.mark_pages_done("doc-1", [2])

    assert inserted == 2
    assert store.page_counts("doc-1") == {"done": 1, "queued": 1}
    tasks = store.list_page_tasks("doc-1")
    assert [task.page_num for task in tasks] == [2, 0]
    assert tasks[0].attempts == 1
    assert tasks[0].scan_round == FormulaScanRound.PDF_STRUCTURE.value
    assert store.round_counts("doc-1") == {
        "r0_pdf_structure:done": 1,
        "r0_pdf_structure:queued": 1,
    }


def test_formula_index_flow_queues_page_scans_without_start_when_budget_zero(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    started: list[tuple[str, int]] = []
    flow._start_next_page_scan_batch = lambda filepath, batch_budget: started.append((filepath, batch_budget))  # type: ignore[method-assign]

    queued = flow.enqueue_page_scans(
        "paper.pdf",
        range(3),
        [_formula("p0_b1", 0)],
        doc_hash="doc-1",
        batch_budget=0,
    )

    assert queued == 3
    assert started == [("paper.pdf", 0)]
    assert flow._queued_page_nums == [0, 1, 2]
    assert store.page_counts("doc-1") == {"queued": 3}
    assert store.round_counts("doc-1") == {"r0_pdf_structure:queued": 3}


def test_formula_index_flow_does_not_queue_done_page_scan(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.enqueue_pages("doc-1", "paper.pdf", [0])
    store.mark_pages_done("doc-1", [0])
    flow = FormulaIndexFlow(store=store)

    queued = flow.enqueue_page_scans(
        "paper.pdf",
        [0],
        [_formula("p0_b1", 0)],
        doc_hash="doc-1",
        batch_budget=0,
    )
    started = flow.start_page_scan_batch(
        "paper.pdf",
        "doc-1",
        [_formula("p0_b1", 0)],
        batch_budget=1,
    )

    assert queued == 0
    assert started == 0
    assert flow._queued_page_nums == []
    assert store.page_counts("doc-1") == {"done": 1}


def test_formula_index_flow_starts_one_persisted_page_batch(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.enqueue_pages("doc-1", "paper.pdf", [0, 1, 2])
    flow = FormulaIndexFlow(store=store)
    started: list[tuple[str, int, list[int]]] = []

    def fake_start(filepath: str, batch_budget: int) -> None:
        started.append((filepath, batch_budget, list(flow._queued_page_nums)))

    flow._start_next_page_scan_batch = fake_start  # type: ignore[method-assign]

    count = flow.start_page_scan_batch(
        "paper.pdf",
        "doc-1",
        [_formula("p0_b1", 0)],
        allowed_pages={1, 2},
        priority_pages={2},
        batch_budget=1,
    )

    assert count == 1
    assert started == [("paper.pdf", 1, [2, 1])]


def test_formula_index_flow_records_page_scan_result(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.enqueue_pages("doc-1", "paper.pdf", [0, 1])
    flow = FormulaIndexFlow(store=store)
    detected: list[list[dict[str, object]]] = []
    flow.formula_blocks_detected.connect(lambda items: detected.append(items))

    flow._on_page_scan_finished(
        {
            "doc_hash": "doc-1",
            "done_pages": [0],
            "failed": [{"page_num": 1, "error": "mfd_failed"}],
            "detected": [{"id": "p0_b2", "page_num": 0, "block_type": "formula"}],
        },
        "paper.pdf",
        1,
    )

    assert store.page_counts("doc-1") == {"done": 1, "failed": 1}
    assert store.page_pending_count("doc-1") == 0
    assert detected == [[{"id": "p0_b2", "page_num": 0, "block_type": "formula"}]]


def test_formula_index_flow_persists_born_digital_structure_candidates(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.enqueue_pages("doc-1", "paper.pdf", [0])
    flow = FormulaIndexFlow(store=store)

    flow._on_page_scan_finished(
        {
            "doc_hash": "doc-1",
            "scan_round": FormulaScanRound.PDF_STRUCTURE.value,
            "done_pages": [0],
            "failed": [],
            "detected": [],
            "structure_candidates": [
                {
                    "candidate_id": "p0_r0_0",
                    "page_num": 0,
                    "bbox": (10, 20, 110, 40),
                    "text": "QKT",
                    "latex": r"Q K^{T}",
                    "score": 0.87,
                    "input_hash": "glyph-hash-1",
                    "model": "pymupdf_born_digital_structure",
                    "model_version": "pymupdf_rawdict_layout_v1",
                    "preprocess_version": "glyph-vector-json-v1",
                    "warnings": ["review_only"],
                    "evidence": {"source": "pdf_structure_display_region"},
                }
            ],
        },
        "paper.pdf",
        1,
    )

    result = store.get_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_r0_0",
        stage="pdf_structure",
        model="pymupdf_born_digital_structure",
        model_version="pymupdf_rawdict_layout_v1",
        preprocess_version="glyph-vector-json-v1",
        input_hash="glyph-hash-1",
    )
    assert result is not None
    assert result.latex == r"Q K^{T}"
    assert result.accepted is False
    assert result.score == 0.87
    assert result.warnings == ("review_only",)
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.PDF_STRUCTURE,
    )
    assert any(
        record.target_id == "p0_r0_0"
        and record.status == "done"
        and record.result_json["input_hash"] == "glyph-hash-1"
        for record in records
    )
    assert store.round_pending_count(
        "doc-1",
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
    ) == 1


def test_formula_index_flow_keeps_high_confidence_structure_candidates_out_of_r2(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.enqueue_pages("doc-1", "paper.pdf", [0])
    flow = FormulaIndexFlow(store=store)

    flow._on_page_scan_finished(
        {
            "doc_hash": "doc-1",
            "scan_round": FormulaScanRound.PDF_STRUCTURE.value,
            "done_pages": [0],
            "failed": [],
            "detected": [],
            "structure_candidates": [
                {
                    "candidate_id": "p0_r0_0",
                    "page_num": 0,
                    "bbox": (10, 20, 110, 40),
                    "text": "x+y",
                    "latex": r"x+y",
                    "score": 0.92,
                    "input_hash": "glyph-hash-1",
                    "model": "pymupdf_born_digital_structure",
                    "model_version": "pymupdf_rawdict_layout_v1",
                    "preprocess_version": "glyph-vector-json-v1",
                    "warnings": [],
                    "evidence": {"source": "pdf_structure_display_region"},
                }
            ],
        },
        "paper.pdf",
        1,
    )

    assert store.round_counts("doc-1") == {"r0_pdf_structure:done": 2}
    assert store.round_pending_count(
        "doc-1",
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
    ) == 0


def test_formula_index_flow_does_not_auto_retry_failed_page_scans(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.enqueue_pages("doc-1", "paper.pdf", [0])
    store.mark_page_failed("doc-1", 0, "mfd_failed")
    flow = FormulaIndexFlow(store=store)

    started = flow.start_page_scan_batch(
        "paper.pdf",
        "doc-1",
        [_formula("p0_b1", 0)],
        batch_budget=1,
    )

    assert started == 0


def test_formula_page_scan_worker_maps_new_and_existing_formula_infos() -> None:
    formula = _formula("p0_b0", 0)
    paragraph = DocumentBlock(
        id="p0_b1",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(200, 200, 300, 240),
    )
    worker = _FormulaPageScanWorker("paper.pdf", [0], [formula, paragraph])

    infos = worker._build_formula_infos(
        0,
        [
            {"bbox": (0, 0, 90, 20), "score": 0.9},
            {"bbox": (205, 205, 295, 235), "score": 0.85},
            {"bbox": (400, 400, 500, 430), "score": 0.8},
        ],
    )

    assert infos[0]["id"] == "p0_b0"
    assert infos[0]["bbox"] == (0.0, 0.0, 90.0, 20.0)
    assert infos[0]["metadata"]["needs_ocr"] is True
    assert infos[1]["id"] == "p0_b1"
    assert infos[1]["block_type"] == "formula"
    assert infos[1]["bbox"] == (205.0, 205.0, 295.0, 235.0)
    assert infos[1]["metadata"]["needs_ocr"] is True
    assert infos[2]["id"] == "p0_b2"
    assert infos[2]["is_new"] is True


def test_pdf_structure_page_worker_does_not_load_mfd(monkeypatch) -> None:
    import sys
    from types import ModuleType

    import src.app.formula_index_flow as module

    worker = module._FormulaPageScanWorker(
        "paper.pdf",
        [0],
        [],
        doc_hash="doc-1",
        scan_round=FormulaScanRound.PDF_STRUCTURE.value,
    )
    emitted: list[dict[str, object]] = []
    worker.finished_signal.connect(lambda payload: emitted.append(payload))

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> object:
            return object()

        def close(self) -> None:
            pass

    class FakeStructureExtractor:
        def extract_page(self, page: object, page_num: int, existing_ids: set[str] | None = None) -> list[object]:
            return []

    class ForbiddenDetector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("r0 must not initialize MFD")

    fake_fitz = ModuleType("fitz")
    fake_fitz.open = lambda path: FakeDoc()  # type: ignore[attr-defined]
    fake_structure_module = ModuleType("src.core.born_digital_formula_extractor")
    fake_structure_module.BornDigitalFormulaStructureExtractor = FakeStructureExtractor  # type: ignore[attr-defined]
    fake_detector_module = ModuleType("src.core.formula_detector")
    fake_detector_module.Pix2TextMFDDetector = ForbiddenDetector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "src.core.born_digital_formula_extractor", fake_structure_module)
    monkeypatch.setitem(sys.modules, "src.core.formula_detector", fake_detector_module)

    worker.run()

    assert emitted[0]["done_pages"] == [0]
    assert emitted[0]["detected"] == []


def test_non_structure_page_worker_can_run_mfd(monkeypatch) -> None:
    import sys
    from types import ModuleType

    import src.app.formula_index_flow as module

    worker = module._FormulaPageScanWorker(
        "paper.pdf",
        [0],
        [],
        doc_hash="doc-1",
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
    )
    emitted: list[dict[str, object]] = []
    worker.finished_signal.connect(lambda payload: emitted.append(payload))

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> object:
            return object()

        def close(self) -> None:
            pass

    class FakeStructureExtractor:
        def extract_page(self, page: object, page_num: int, existing_ids: set[str] | None = None) -> list[object]:
            return []

    class FakeDetector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def detect_specific_pages(self, doc: object, pages: list[int]) -> list[dict[str, object]]:
            return [{"bbox": (10, 10, 30, 20), "score": 0.8}]

    fake_fitz = ModuleType("fitz")
    fake_fitz.open = lambda path: FakeDoc()  # type: ignore[attr-defined]
    fake_structure_module = ModuleType("src.core.born_digital_formula_extractor")
    fake_structure_module.BornDigitalFormulaStructureExtractor = FakeStructureExtractor  # type: ignore[attr-defined]
    fake_detector_module = ModuleType("src.core.formula_detector")
    fake_detector_module.Pix2TextMFDDetector = FakeDetector  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "src.core.born_digital_formula_extractor", fake_structure_module)
    monkeypatch.setitem(sys.modules, "src.core.formula_detector", fake_detector_module)

    worker.run()

    assert emitted[0]["done_pages"] == [0]
    assert emitted[0]["detected"][0]["metadata"]["mfd_page_scan"] is True


def test_formula_index_store_persists_status_transitions(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula("p0_b1", 0, score=0.7)

    inserted = store.enqueue_blocks("doc-1", "paper.pdf", [block], priority_pages={0})
    store.mark_running("doc-1", ["p0_b1"])
    store.mark_done("doc-1", "p0_b1", r"\frac{a}{b}", "hash-1")

    tasks = store.list_tasks("doc-1")
    assert inserted == 1
    assert store.counts("doc-1") == {"done": 1}
    assert tasks[0].status == "done"
    assert tasks[0].attempts == 1
    assert tasks[0].latex == r"\frac{a}{b}"
    assert tasks[0].image_hash == "hash-1"
    assert tasks[0].scan_round == FormulaScanRound.CACHED_RECOGNITION.value
    assert store.round_counts("doc-1") == {"r1_cached_recognition:done": 1}


def test_formula_index_store_requeues_changed_done_block(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula("p0_b1", 0)
    store.enqueue_blocks("doc-1", "paper.pdf", [block])
    store.mark_done("doc-1", "p0_b1", r"x", "hash-1")

    changed = block.model_copy(update={"content": "changed placeholder"})
    store.enqueue_blocks("doc-1", "paper.pdf", [changed])

    assert store.counts("doc-1") == {"queued": 1}
    task = store.list_tasks("doc-1")[0]
    assert task.latex == r"x"
    assert task.status == "queued"


def test_formula_index_store_keeps_high_precision_round_after_cached_done(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula("p0_b1", 0)
    store.enqueue_blocks(
        "doc-1",
        "paper.pdf",
        [block],
        scan_round=FormulaScanRound.CACHED_RECOGNITION,
    )
    store.mark_done(
        "doc-1",
        "p0_b1",
        r"\alpha",
        "hash-1",
        scan_round=FormulaScanRound.CACHED_RECOGNITION,
    )

    queued = store.enqueue_blocks(
        "doc-1",
        "paper.pdf",
        [block],
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
    )

    assert queued == 1
    assert store.counts("doc-1") == {"queued": 1}
    assert store.list_tasks(
        "doc-1",
        statuses={"queued"},
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
    )[0].block_id == "p0_b1"
    assert store.round_counts("doc-1") == {
        "r1_cached_recognition:done": 1,
        "r2_local_high_precision:queued": 1,
    }


def test_formula_index_flow_records_high_precision_worker_round(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    block = _formula("p0_b1", 0)
    flow.enqueue_blocks(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        batch_budget=0,
        cache_only=False,
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
    )

    flow._on_worker_finished(
        {
            "doc_hash": "doc-1",
            "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
            "updated": [],
            "pending": 0,
            "done": [{
                "block_id": "p0_b1",
                "latex": r"\beta",
                "image_hash": "hash-2",
                "model": "pix2text-mfr",
                "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
            }],
            "skipped": [],
            "failed": [],
        },
        "paper.pdf",
        1,
    )

    assert store.round_counts("doc-1") == {
        "r2_local_high_precision:done": 1,
    }
    record = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
    )[0]
    assert record.result_json["latex"] == r"\beta"
    assert record.result_json["image_hash"] == "hash-2"
    results = store.list_recognition_results(
        "doc-1",
        candidate_id="p0_b1",
        stage="local_precise",
    )
    assert len(results) == 1
    assert results[0].latex == r"\beta"
    assert results[0].accepted is False


def test_formula_index_flow_records_cached_round_as_accepted_result(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    block = _formula("p0_b1", 0)
    flow.enqueue_blocks(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        batch_budget=0,
    )

    flow._on_worker_finished(
        {
            "doc_hash": "doc-1",
            "updated": [],
            "pending": 0,
            "done": [{
                "block_id": "p0_b1",
                "latex": r"\alpha",
                "image_hash": "hash-1",
                "model": "pix2text-mfr",
            }],
            "skipped": [],
            "failed": [],
        },
        "paper.pdf",
        1,
    )

    results = store.list_recognition_results("doc-1", candidate_id="p0_b1")
    assert len(results) == 1
    assert results[0].stage == "local_fast"
    assert results[0].accepted is True


def test_formula_recognition_results_use_exact_input_hash_for_cache(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))

    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="pdf_structure",
        model="mupdf_rawdict",
        model_version="fitz-1",
        preprocess_version="glyph-json-v1",
        input_hash="input-1",
        latex=r"\gamma",
        score=0.91,
        warnings=["low_confidence"],
        evidence={"page": 0},
    )

    cached = store.get_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="pdf_structure",
        model="mupdf_rawdict",
        model_version="fitz-1",
        preprocess_version="glyph-json-v1",
        input_hash="input-1",
    )
    miss = store.get_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="pdf_structure",
        model="mupdf_rawdict",
        model_version="fitz-1",
        preprocess_version="glyph-json-v1",
        input_hash="input-2",
    )

    assert cached is not None
    assert cached.result_id == result_id
    assert cached.latex == r"\gamma"
    assert cached.score == 0.91
    assert cached.warnings == ("low_confidence",)
    assert cached.evidence == {"page": 0}
    assert miss is None


def test_formula_recognition_results_keep_one_accepted_per_candidate(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="local_fast",
        model="pix2text-mfr",
        input_hash="image-1",
        latex=r"\alpha",
        accepted=True,
    )

    store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id="p0_b1",
        stage="cloud_semantic",
        model="deepseek",
        model_version="v4",
        input_hash="prompt-1",
        latex=r"\beta",
        accepted=True,
    )

    accepted = store.list_recognition_results("doc-1", candidate_id="p0_b1", accepted=True)
    all_results = store.list_recognition_results("doc-1", candidate_id="p0_b1")
    assert len(accepted) == 1
    assert accepted[0].stage == "cloud_semantic"
    assert accepted[0].latex == r"\beta"
    assert len(all_results) == 2


def test_formula_index_flow_queues_semantic_review_for_all_formula_blocks(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    done_formula = _formula("p0_done", 0, needs_ocr=False)
    pending_formula = _formula("p1_pending", 1)
    paragraph = DocumentBlock(
        id="p0_p",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="not formula",
        bbox=(0, 0, 10, 10),
    )

    queued = flow.enqueue_semantic_review_blocks(
        "paper.pdf",
        "doc-1",
        [done_formula, pending_formula, paragraph],
        priority_pages={1},
    )

    assert queued == 2
    assert store.round_counts("doc-1") == {
        "r3_cloud_semantic_review:queued": 2,
    }
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
    )
    assert [record.target_id for record in records] == ["p1_pending", "p0_done"]
    assert store.round_pending_count(
        "doc-1",
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
    ) == 2


def test_formula_index_flow_records_worker_result_in_store(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    block = _formula("p0_b1", 0)
    flow.enqueue_blocks(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        batch_budget=0,
    )

    updated = block.model_copy(update={
        "content": r"\sum_i x_i",
        "metadata": {**block.metadata, "needs_ocr": False, "mfr_recognized": True},
    })
    flow._on_worker_finished(
        {
            "doc_hash": "doc-1",
            "updated": [updated],
            "pending": 0,
            "done": [{
                "block_id": "p0_b1",
                "latex": r"\sum_i x_i",
                "image_hash": "hash-2",
                "model": "pix2text-mfr",
            }],
            "skipped": [],
            "failed": [],
        },
        "paper.pdf",
        1,
    )

    assert store.counts("doc-1") == {"done": 1}
    assert store.list_tasks("doc-1")[0].latex == r"\sum_i x_i"


def test_high_precision_worker_outputs_candidate_only(monkeypatch, tmp_path) -> None:
    import hashlib
    import sys
    from types import ModuleType

    import src.app.formula_index_flow as module

    block = _formula("p0_b1", 0)
    worker = module._FormulaOcrWorker(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        cache_only=False,
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
    )
    emitted: list[dict[str, object]] = []
    worker.finished_signal.connect(lambda payload: emitted.append(payload))

    class FakeDoc:
        def close(self) -> None:
            pass

    class FakeDetector:
        @staticmethod
        def _crop_bbox_image(doc: object, page_num: int, bbox: tuple[float, float, float, float], dpi: int, pad: float) -> bytes:
            return b"png-bytes"

        @staticmethod
        def _normalize_latex(latex: str) -> str:
            return latex.strip()

    class FakeMathOCR:
        def recognize_batch(self, images: list[bytes], max_uncached: int = 0) -> list[str]:
            assert max_uncached == 1
            return [r"\int_0^1 x dx"]

    fake_fitz = ModuleType("fitz")
    fake_fitz.open = lambda path: FakeDoc()  # type: ignore[attr-defined]
    fake_detector_module = ModuleType("src.core.formula_detector")
    fake_detector_module.Pix2TextMFDDetector = FakeDetector  # type: ignore[attr-defined]
    fake_math_ocr_module = ModuleType("src.core.math_ocr")
    fake_math_ocr_module.MathOCR = FakeMathOCR  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "src.core.formula_detector", fake_detector_module)
    monkeypatch.setitem(sys.modules, "src.core.math_ocr", fake_math_ocr_module)

    worker.run()

    assert len(emitted) == 1
    assert emitted[0]["updated"] == []
    assert emitted[0]["done"] == [{
        "block_id": "p0_b1",
        "latex": r"\int_0^1 x dx",
        "image_hash": hashlib.sha256(b"png-bytes").hexdigest(),
        "model": "pix2text-mfr",
        "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
    }]


def test_high_precision_worker_appends_external_tool_candidates(monkeypatch) -> None:
    import hashlib
    import sys
    from types import ModuleType

    import src.app.formula_index_flow as module
    from src.core.external_formula_tools import ExternalFormulaCandidate

    block = _formula("p0_b1", 0)
    worker = module._FormulaOcrWorker(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        cache_only=False,
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
    )
    emitted: list[dict[str, object]] = []
    worker.finished_signal.connect(lambda payload: emitted.append(payload))

    class FakeDoc:
        def close(self) -> None:
            pass

    class FakeDetector:
        @staticmethod
        def _crop_bbox_image(doc: object, page_num: int, bbox: tuple[float, float, float, float], dpi: int, pad: float) -> bytes:
            return b"png-bytes"

        @staticmethod
        def _normalize_latex(latex: str) -> str:
            return latex.strip()

    class FakeMathOCR:
        def recognize_batch(self, images: list[bytes], max_uncached: int = 0) -> list[str]:
            return [r"\alpha"]

    class FakeExternalRunner:
        def recognize_images(
            self,
            images: list[tuple[str, bytes]],
            specs: object = None,
        ) -> list[ExternalFormulaCandidate]:
            assert images == [("p0_b1", b"png-bytes")]
            assert specs is None
            return [
                ExternalFormulaCandidate(
                    candidate_id="p0_b1",
                    latex=r"\beta",
                    model="paddle_formula",
                    model_version="PP-FormulaNet_plus-S",
                    preprocess_version="png-v1",
                    score=0.42,
                    duration_ms=123,
                    warnings=("candidate_only",),
                ),
                ExternalFormulaCandidate(
                    candidate_id="p0_b1",
                    latex=r"\gamma",
                    model="pix2text_formula",
                    model_version="pix2text",
                    preprocess_version="png-v1",
                    score=0.91,
                    duration_ms=456,
                ),
            ]

    fake_fitz = ModuleType("fitz")
    fake_fitz.open = lambda path: FakeDoc()  # type: ignore[attr-defined]
    fake_detector_module = ModuleType("src.core.formula_detector")
    fake_detector_module.Pix2TextMFDDetector = FakeDetector  # type: ignore[attr-defined]
    fake_math_ocr_module = ModuleType("src.core.math_ocr")
    fake_math_ocr_module.MathOCR = FakeMathOCR  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "src.core.formula_detector", fake_detector_module)
    monkeypatch.setitem(sys.modules, "src.core.math_ocr", fake_math_ocr_module)
    monkeypatch.setattr(module, "ExternalFormulaToolRunner", FakeExternalRunner)

    worker.run()

    image_hash = hashlib.sha256(b"png-bytes").hexdigest()
    assert emitted[0]["updated"] == []
    assert emitted[0]["done"] == [
        {
            "block_id": "p0_b1",
            "latex": r"\alpha",
            "image_hash": image_hash,
            "model": "pix2text-mfr",
            "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
        },
        {
            "block_id": "p0_b1",
            "latex": r"\beta",
            "normalized_latex": r"\beta",
            "image_hash": image_hash,
            "model": "paddle_formula",
            "model_version": "PP-FormulaNet_plus-S",
            "preprocess_version": "png-v1",
            "score": 0.42,
            "duration_ms": 123,
            "warnings": ["candidate_only"],
            "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
        },
        {
            "block_id": "p0_b1",
            "latex": r"\gamma",
            "normalized_latex": r"\gamma",
            "image_hash": image_hash,
            "model": "pix2text_formula",
            "model_version": "pix2text",
            "preprocess_version": "png-v1",
            "score": 0.91,
            "duration_ms": 456,
            "warnings": [],
            "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
        },
    ]


def test_high_precision_external_candidates_are_persisted_unaccepted(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    flow = FormulaIndexFlow(store=store)
    block = _formula("p0_b1", 0)
    flow.enqueue_blocks(
        "paper.pdf",
        [block],
        doc_hash="doc-1",
        batch_budget=0,
        cache_only=False,
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
    )

    flow._on_worker_finished(
        {
            "doc_hash": "doc-1",
            "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
            "updated": [],
            "pending": 1,
            "done": [
                {
                    "block_id": "p0_b1",
                    "latex": r"\alpha",
                    "image_hash": "hash-1",
                    "model": "pix2text-mfr",
                    "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
                },
                {
                    "block_id": "p0_b1",
                    "latex": r"\beta",
                    "normalized_latex": r"\beta",
                    "image_hash": "hash-1",
                    "model": "paddle_formula",
                    "model_version": "PP-FormulaNet_plus-S",
                    "preprocess_version": "png-v1",
                    "score": 0.42,
                    "duration_ms": 123,
                    "warnings": ["candidate_only"],
                    "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
                },
            ],
            "skipped": [],
            "failed": [],
        },
        "paper.pdf",
        1,
    )

    results = store.list_recognition_results(
        "doc-1",
        candidate_id="p0_b1",
        stage="local_precise",
    )
    by_model = {result.model: result for result in results}
    assert set(by_model) == {"pix2text-mfr", "paddle_formula"}
    assert by_model["paddle_formula"].accepted is False
    assert by_model["paddle_formula"].score == 0.42
    assert by_model["paddle_formula"].duration_ms == 123
    assert by_model["paddle_formula"].warnings == ("candidate_only",)


class _Signal:
    def connect(self, callback: object) -> None:
        pass
