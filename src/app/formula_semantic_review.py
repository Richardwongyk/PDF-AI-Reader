"""Cloud semantic review for formula candidates.

This module consumes the persisted ``r3_cloud_semantic_review`` queue and writes
review candidates back to ``FormulaIndexStore``. It deliberately does not
replace ``DocumentBlock.content``; accepted writeback needs a separate evidence
gate.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from src.app.formula_index_store import FormulaIndexStore, FormulaRoundRecord, FormulaScanRound
from src.core.ai_engine import BaseLLMClient
from src.core.models import BlockType, DocumentBlock


@dataclass(frozen=True)
class FormulaSemanticReviewResult:
    """Structured cloud review candidate."""

    target_id: str
    suggested_latex: str
    should_replace: bool
    confidence: float
    reason: str
    risks: list[str]
    raw_response: str

    def to_json(self) -> dict[str, object]:
        return {
            "suggested_latex": self.suggested_latex,
            "should_replace": self.should_replace,
            "confidence": self.confidence,
            "reason": self.reason,
            "risks": self.risks,
            "raw_response": self.raw_response,
        }


class FormulaSemanticReviewService:
    """Review queued formula blocks with a reasoning LLM."""

    def __init__(
        self,
        store: FormulaIndexStore,
        client: BaseLLMClient,
        batch_size: int = 4,
        timeout_sec: int = 90,
    ) -> None:
        self._store = store
        self._client = client
        self._batch_size = max(1, int(batch_size))
        self._timeout_sec = max(5, int(timeout_sec))

    def pending_count(self, doc_hash: str) -> int:
        return self._store.round_pending_count(
            doc_hash,
            scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        )

    def run_batch(
        self,
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> dict[str, int]:
        """Run one bounded semantic-review batch.

        Returns status counts for this call. The caller decides when to schedule
        the next batch, so this method never drains the whole document by
        default.
        """
        block_map = {block.id: block for block in blocks}
        batch_limit = self._batch_size if limit is None else max(0, int(limit))
        if not doc_hash or batch_limit <= 0:
            return {"done": 0, "failed": 0, "skipped": 0}
        records = self._store.list_round_records(
            doc_hash,
            statuses={"queued"},
            scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
            limit=batch_limit,
        )
        if not records:
            return {"done": 0, "failed": 0, "skipped": 0}
        self._store.mark_round_running(
            doc_hash,
            FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
            "block",
            [record.target_id for record in records],
        )
        counts = {"done": 0, "failed": 0, "skipped": 0}
        for record in records:
            block = block_map.get(record.target_id)
            started = time.perf_counter()
            if block is None or block.block_type != BlockType.FORMULA:
                self._store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                    "block",
                    record.target_id,
                    "missing_formula_block",
                    status="skipped",
                )
                counts["skipped"] += 1
                continue
            try:
                result = self.review_block(record, block)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._store.mark_round_done(
                    doc_hash,
                    FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                    "block",
                    record.target_id,
                    result.to_json(),
                    elapsed_ms=elapsed_ms,
                )
                counts["done"] += 1
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                    "block",
                    record.target_id,
                    str(exc),
                    elapsed_ms=elapsed_ms,
                )
                counts["failed"] += 1
        return counts

    def review_block(
        self,
        record: FormulaRoundRecord,
        block: DocumentBlock,
    ) -> FormulaSemanticReviewResult:
        messages = self._build_messages(block)
        raw = self._client.generate(
            messages,
            temperature=0,
            max_tokens=700,
            timeout=self._timeout_sec,
        )
        parsed = _parse_review_json(raw)
        suggested = str(parsed.get("suggested_latex", "") or "").strip()
        return FormulaSemanticReviewResult(
            target_id=record.target_id,
            suggested_latex=suggested,
            should_replace=bool(parsed.get("should_replace", False)),
            confidence=_bounded_float(parsed.get("confidence", 0.0)),
            reason=str(parsed.get("reason", "") or "").strip(),
            risks=[
                str(item)
                for item in parsed.get("risks", [])
                if isinstance(item, str)
            ],
            raw_response=raw,
        )

    @staticmethod
    def _build_messages(block: DocumentBlock) -> list[dict[str, str]]:
        context = {
            "block_id": block.id,
            "page": block.page_num + 1,
            "content": block.content,
            "bbox": list(block.bbox),
            "section_title": block.section_title,
            "metadata": block.metadata,
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是数学公式 LaTeX 语义复核器。只根据给定 PDF 证据和上下文提出候选修正，"
                    "不要凭空生成新公式。输出必须是 JSON。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请复核下面公式块。若可见证据不足，请 should_replace=false。\n"
                    "JSON 字段必须包含 suggested_latex, should_replace, confidence, reason, risks。\n"
                    f"公式块证据: {json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ]


class FormulaSemanticReviewFlow(QObject):
    """Run bounded r3 formula semantic review batches off the UI thread."""

    review_finished = Signal(dict)

    def __init__(
        self,
        service_factory: Callable[[], FormulaSemanticReviewService],
        parent: QObject | None = None,
        store: FormulaIndexStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._service_factory = service_factory
        self._store = store or FormulaIndexStore()
        self._thread: _FormulaSemanticReviewWorker | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.isRunning())

    def pending_count(self, doc_hash: str) -> int:
        if not doc_hash:
            return 0
        return self._store.round_pending_count(
            doc_hash,
            scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        )

    def start_batch(
        self,
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> bool:
        if not doc_hash or not blocks or self.is_running:
            return False
        if self.pending_count(doc_hash) <= 0:
            return False
        self._thread = _FormulaSemanticReviewWorker(
            service_factory=self._service_factory,
            doc_hash=doc_hash,
            blocks=blocks,
            limit=limit,
        )
        self._thread.finished_signal.connect(self._on_worker_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_worker_thread_done)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.quit()
            self._thread.wait(1500)
        self._thread = None

    def _on_worker_finished(self, result: dict[str, object]) -> None:
        self.review_finished.emit(result)

    def _on_worker_thread_done(self) -> None:
        self._thread = None


class _FormulaSemanticReviewWorker(QThread):
    """Consume one persisted r3 queue batch in a background thread."""

    finished_signal = Signal(dict)

    def __init__(
        self,
        service_factory: Callable[[], FormulaSemanticReviewService],
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> None:
        super().__init__()
        self._service_factory = service_factory
        self._doc_hash = doc_hash
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._limit = limit

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                self.finished_signal.emit({
                    "doc_hash": self._doc_hash,
                    "done": 0,
                    "failed": 0,
                    "skipped": 0,
                    "pending": 0,
                })
                return
            service = self._service_factory()
            counts = service.run_batch(self._doc_hash, self._blocks, limit=self._limit)
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                **counts,
                "pending": service.pending_count(self._doc_hash),
            })
        except Exception as exc:
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "done": 0,
                "failed": 1,
                "skipped": 0,
                "pending": 0,
                "error": str(exc),
            })


def _parse_review_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("semantic review response is not JSON")
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("semantic review response must be a JSON object")
    return value


def _bounded_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
