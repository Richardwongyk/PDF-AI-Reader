from src.app.formula_index_flow import FormulaIndexFlow, _FormulaPageScanWorker
from src.app.formula_index_scheduler import FormulaScanPlan
from src.app.formula_index_store import FormulaIndexStore
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
        ) -> None:
            self.filepath = filepath
            self.blocks = blocks
            self.doc_hash = doc_hash
            self.cache_only = cache_only
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
        ) -> None:
            self.doc_hash = doc_hash
            self.cache_only = cache_only
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
    )

    queued = flow.persist_plan("paper.pdf", "doc-1", plan)

    assert queued == 1
    assert started == []
    assert flow._queued_blocks == []
    assert store.counts("doc-1") == {"queued": 1}


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


class _Signal:
    def connect(self, callback: object) -> None:
        pass
