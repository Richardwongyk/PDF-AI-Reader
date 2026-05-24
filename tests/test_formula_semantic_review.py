import json

from PySide6.QtCore import QCoreApplication, QTimer

from src.app import formula_semantic_review as semantic_module
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.formula_semantic_review import FormulaSemanticReviewFlow, FormulaSemanticReviewService
from src.core.ai_engine import BaseLLMClient
from src.core.models import BlockType, DocumentBlock


def _formula(block_id: str = "p0_b1") -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$\nA=softmax(QK T / sqrt(d_k))V\n$$",
        bbox=(10, 20, 100, 40),
        section_title="Attention",
        metadata={"source": "pdf_structure_display_region", "needs_ocr": False},
    )


class _ReviewClient(BaseLLMClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.messages: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]], **kwargs: object) -> str:
        self.messages.append(messages)
        return self.response

    def generate_stream(self, messages: list[dict[str, str]], **kwargs: object):
        yield self.response

    @property
    def model_name(self) -> str:
        return "fake-reasoning"

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def check_availability(self) -> bool:
        return True


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self._callbacks.append(callback)

    def emit(self, *args: object) -> None:
        for callback in list(self._callbacks):
            callback(*args)  # type: ignore[misc]


def test_formula_semantic_review_writes_candidate_without_overwriting_block(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    response = json.dumps({
        "suggested_latex": r"$$\nA=\operatorname{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V\n$$",
        "should_replace": True,
        "confidence": 0.83,
        "reason": "上下文和可见符号一致",
        "risks": ["needs_gate"],
    }, ensure_ascii=False)
    client = _ReviewClient(response)
    service = FormulaSemanticReviewService(store, client, batch_size=2)

    counts = service.run_batch("doc-1", [block])

    assert counts == {"done": 1, "failed": 0, "skipped": 0}
    assert block.content == r"$$\nA=softmax(QK T / sqrt(d_k))V\n$$"
    record = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
    )[0]
    assert record.status == "done"
    assert record.attempts == 1
    assert record.result_json["should_replace"] is True
    assert record.result_json["confidence"] == 0.83
    assert record.result_json["input_hash"]
    assert record.result_json["model"] == "fake-reasoning"
    assert record.result_json["model_version"] == "fake-reasoning"
    assert record.result_json["stage"] == FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value
    assert r"\sqrt{d_k}" in str(record.result_json["suggested_latex"])
    assert "公式块证据" in client.messages[0][1]["content"]


def test_formula_semantic_review_prompt_includes_candidate_and_fusion_evidence(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake_tool",
        model_version="v1",
        preprocess_version="png",
        input_hash="image-hash",
        latex=r"\alpha+\beta",
        normalized_latex=r"\alpha+\beta",
        score=0.98,
        warnings=["candidate_only"],
        evidence={"page_num": 0, "bbox": [10, 20, 100, 40]},
    )
    store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="formula_candidate_fusion_v1",
        input_hash="fusion-hash",
        best_result_id=result_id,
        ranked_result_ids=[result_id],
        agreement_score=1.0,
        source_similarity=0.99,
        syntax_valid=True,
        decision="ready_for_manual_accept",
        result_json={"best_latex": r"\alpha+\beta"},
    )
    client = _ReviewClient(
        '{"suggested_latex":"","should_replace":false,"confidence":0,"reason":"evidence","risks":[]}'
    )
    service = FormulaSemanticReviewService(store, client, batch_size=1)

    service.run_batch("doc-1", [block])

    prompt = client.messages[0][1]["content"]
    assert "recognition_candidates" in prompt
    assert "fusion_records" in prompt
    assert result_id in prompt
    assert "ready_for_manual_accept" in prompt


def test_formula_semantic_review_skips_missing_block(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula("p0_missing")
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    service = FormulaSemanticReviewService(
        store,
        _ReviewClient('{"suggested_latex":"","should_replace":false,"confidence":0,"reason":"","risks":[]}'),
    )

    counts = service.run_batch("doc-1", [])

    assert counts == {"done": 0, "failed": 0, "skipped": 1}
    record = store.list_round_records("doc-1")[0]
    assert record.status == "skipped"
    assert record.error == "missing_formula_block"


def test_formula_semantic_review_uses_payload_candidate_for_inline_targets(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    target = DocumentBlock(
        id="p0_b0_inline_0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"x_i",
        bbox=(0, 0, 10, 10),
    )
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [target],
        result_json_by_target={
            target.id: {
                "input_hash": "fusion-hash",
                "decision": "needs_more_evidence",
                "review_candidate": {
                    "latex": r"x_i",
                    "page_num": 0,
                    "bbox": [1, 2, 3, 4],
                    "source": "paragraph_inline_math",
                },
            }
        },
    )
    client = _ReviewClient(
        '{"suggested_latex":"x_i","should_replace":false,"confidence":0.7,"reason":"inline","risks":[]}'
    )
    service = FormulaSemanticReviewService(store, client, batch_size=1)

    counts = service.run_batch("doc-1", [])

    assert counts == {"done": 1, "failed": 0, "skipped": 0}
    record = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
    )[0]
    assert record.status == "done"
    assert record.result_json["suggested_latex"] == "x_i"
    assert "paragraph_inline_math" in client.messages[0][1]["content"]


def test_formula_semantic_review_keeps_string_risk_as_single_item(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    response = json.dumps({
        "suggested_latex": "",
        "should_replace": False,
        "confidence": 0.0,
        "reason": "evidence is insufficient",
        "risks": "insufficient_evidence",
    })
    service = FormulaSemanticReviewService(store, _ReviewClient(response), batch_size=1)

    service.run_batch("doc-1", [block])

    record = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
    )[0]
    assert record.result_json["risks"] == ["insufficient_evidence"]


def test_formula_semantic_review_records_bad_json_failure(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    service = FormulaSemanticReviewService(store, _ReviewClient("not json"))

    counts = service.run_batch("doc-1", [block])

    assert counts == {"done": 0, "failed": 1, "skipped": 0}
    record = store.list_round_records("doc-1")[0]
    assert record.status == "failed"
    assert "JSON" in record.error


def test_formula_semantic_review_respects_batch_limit(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    blocks = [_formula("p0_b1"), _formula("p1_b1")]
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        blocks,
    )
    response = '{"suggested_latex":"x","should_replace":false,"confidence":0.1,"reason":"low","risks":[]}'
    service = FormulaSemanticReviewService(store, _ReviewClient(response), batch_size=1)

    counts = service.run_batch("doc-1", blocks)

    assert counts == {"done": 1, "failed": 0, "skipped": 0}
    assert store.round_counts("doc-1") == {
        "r3_cloud_semantic_review:done": 1,
        "r3_cloud_semantic_review:queued": 1,
    }


def test_formula_semantic_review_flow_starts_bounded_background_batch(tmp_path, monkeypatch) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    response = '{"suggested_latex":"x","should_replace":false,"confidence":0.2,"reason":"candidate","risks":[]}'
    service = FormulaSemanticReviewService(store, _ReviewClient(response), batch_size=1)

    class FakeWorker:
        instances: list["FakeWorker"] = []

        def __init__(
            self,
            service_factory,
            doc_hash: str,
            blocks: list[DocumentBlock],
            limit: int | None = None,
        ) -> None:
            self.service_factory = service_factory
            self.doc_hash = doc_hash
            self.blocks = blocks
            self.limit = limit
            self.finished_signal = _Signal()
            self.finished = _Signal()
            self.running = False
            FakeWorker.instances.append(self)

        def start(self) -> None:
            self.running = True

        def isRunning(self) -> bool:
            return self.running

        def deleteLater(self) -> None:
            pass

        def requestInterruption(self) -> None:
            pass

        def quit(self) -> None:
            self.running = False

        def wait(self, timeout_ms: int) -> None:
            self.running = False

        def run_now(self) -> None:
            review_service = self.service_factory()
            counts = review_service.run_batch(self.doc_hash, self.blocks, limit=self.limit)
            self.finished_signal.emit({
                "doc_hash": self.doc_hash,
                **counts,
                "pending": review_service.pending_count(self.doc_hash),
            })
            self.running = False
            self.finished.emit()

    monkeypatch.setattr(semantic_module, "_FormulaSemanticReviewWorker", FakeWorker)
    flow = FormulaSemanticReviewFlow(lambda: service, store=store)
    results: list[dict[str, object]] = []
    flow.review_finished.connect(results.append)

    assert flow.pending_count("doc-1") == 1
    assert flow.start_batch("doc-1", [block], limit=1) is True
    assert flow.is_running is True
    FakeWorker.instances[0].run_now()

    assert results == [{
        "doc_hash": "doc-1",
        "done": 1,
        "failed": 0,
        "skipped": 0,
        "pending": 0,
    }]
    assert flow.pending_count("doc-1") == 0
    assert store.round_counts("doc-1") == {"r3_cloud_semantic_review:done": 1}


def test_formula_semantic_review_flow_does_not_start_without_pending(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    service = FormulaSemanticReviewService(
        store,
        _ReviewClient('{"suggested_latex":"","should_replace":false,"confidence":0,"reason":"","risks":[]}'),
    )
    flow = FormulaSemanticReviewFlow(lambda: service, store=store)

    assert flow.start_batch("doc-1", [_formula()], limit=1) is False


def test_formula_semantic_review_flow_real_qthread_smoke(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        [block],
    )
    response = '{"suggested_latex":"x","should_replace":false,"confidence":0.2,"reason":"candidate","risks":[]}'
    service = FormulaSemanticReviewService(store, _ReviewClient(response), batch_size=1)
    flow = FormulaSemanticReviewFlow(lambda: service, store=store)
    results: list[dict[str, object]] = []

    def finish(result: dict[str, object]) -> None:
        results.append(result)
        app.quit()

    flow.review_finished.connect(finish)
    assert flow.start_batch("doc-1", [block], limit=1) is True
    QTimer.singleShot(5000, app.quit)
    app.exec()

    assert results
    assert results[0]["done"] == 1
    assert results[0]["pending"] == 0
    assert store.round_counts("doc-1") == {"r3_cloud_semantic_review:done": 1}
