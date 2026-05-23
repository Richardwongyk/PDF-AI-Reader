from src.app.formula_index_flow import FormulaIndexFlow
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
