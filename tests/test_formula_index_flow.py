from src.app.formula_index_flow import FormulaIndexFlow
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
        def __init__(self, filepath: str, blocks: list[DocumentBlock], cache_only: bool = True) -> None:
            self.filepath = filepath
            self.blocks = blocks
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
        def __init__(self, filepath: str, blocks: list[DocumentBlock], cache_only: bool = True) -> None:
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


class _Signal:
    def connect(self, callback: object) -> None:
        pass
