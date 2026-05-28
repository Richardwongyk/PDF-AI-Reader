"""Asynchronous formula OCR indexing flow.

This module keeps formula recognition out of the document open/render path.
It consumes formula blocks that still need OCR, recognizes a small priority
batch, and emits updated blocks so the UI and knowledge index can be refreshed
incrementally.
"""

from __future__ import annotations

import logging
import hashlib
import time

from PySide6.QtCore import QObject, QThread, Signal

from src.app.formula_index_scheduler import FormulaScanPlan
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.core.external_formula_tools import ExternalFormulaToolRunner, ExternalFormulaToolSpec
from src.core.models import BlockType, DocumentBlock, wrap_math_text

_logger = logging.getLogger(__name__)


class FormulaIndexFlow(QObject):
    """Schedule budgeted background OCR for pending formula blocks."""

    formulas_updated = Signal(list)  # list[DocumentBlock]
    formula_blocks_detected = Signal(list)  # list[dict]
    scan_finished = Signal(int, int)  # (recognized, pending)

    DEFAULT_BATCH_BUDGET = 8
    DEFAULT_PAGE_BATCH_BUDGET = 1

    def __init__(
        self,
        parent: QObject | None = None,
        store: FormulaIndexStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._thread: _FormulaOcrWorker | None = None
        self._page_thread: _FormulaPageScanWorker | None = None
        self._queued_blocks: list[DocumentBlock] = []
        self._queued_page_nums: list[int] = []
        self._page_scan_blocks: list[DocumentBlock] = []
        self._drain_queue = False
        self._drain_page_queue = False
        self._cache_only = True
        self._scan_round = FormulaScanRound.CACHED_RECOGNITION.value
        self._page_scan_round = FormulaScanRound.PDF_STRUCTURE.value
        self._store = store or FormulaIndexStore()
        self._active_doc_hash = ""

    @property
    def is_running(self) -> bool:
        return bool(
            (self._thread and self._thread.isRunning())
            or (self._page_thread and self._page_thread.isRunning())
        )

    @property
    def store(self) -> FormulaIndexStore:
        """Return the persistent formula index store shared by formula services."""
        return self._store

    def persist_plan(
        self,
        filepath: str,
        doc_hash: str,
        plan: FormulaScanPlan,
    ) -> int:
        """Persist a scan plan without starting OCR work immediately."""
        if not filepath or not doc_hash or not plan.blocks:
            return 0
        candidates = self._ocr_candidates(plan.blocks)
        queued = self._store.enqueue_blocks(
            doc_hash,
            filepath,
            candidates,
            plan.priority_pages,
            scan_round=plan.scan_round,
        )
        if queued:
            _logger.info("公式索引任务持久化: doc=%s count=%d", doc_hash, queued)
        return queued

    def enqueue_blocks(
        self,
        filepath: str,
        blocks: list[DocumentBlock],
        doc_hash: str = "",
        priority_pages: set[int] | None = None,
        batch_budget: int | None = None,
        drain_queue: bool = False,
        cache_only: bool = True,
        scan_round: str = FormulaScanRound.CACHED_RECOGNITION.value,
    ) -> None:
        """Enqueue formula blocks that still need OCR.

        Jobs are persisted so a later scan can resume after app restart. The
        in-memory queue still controls the currently budgeted worker batch.
        """
        if not filepath:
            return
        self._active_doc_hash = doc_hash
        self._drain_queue = drain_queue
        self._cache_only = cache_only
        self._scan_round = scan_round
        candidates = self._ocr_candidates(blocks)
        if not candidates:
            self.scan_finished.emit(0, 0)
            return
        priority_pages = priority_pages or set()
        if doc_hash:
            queued = self._store.enqueue_blocks(
                doc_hash,
                filepath,
                candidates,
                priority_pages,
                scan_round=scan_round,
            )
            if queued:
                _logger.info("公式索引任务入队: doc=%s count=%d", doc_hash, queued)
            eligible_ids = {
                task.block_id
                for task in self._store.list_tasks(
                    doc_hash,
                    statuses={"queued", "running"},
                    scan_round=scan_round,
                    limit=max(len(candidates), 10000),
                )
            }
            candidates = [block for block in candidates if block.id in eligible_ids]
            if not candidates:
                self.scan_finished.emit(0, self._store.pending_count(doc_hash))
                return
        existing_ids = {block.id for block in self._queued_blocks}
        self._queued_blocks.extend(
            block for block in candidates
            if block.id not in existing_ids
        )
        self._queued_blocks.sort(
            key=lambda block: self._priority_key(block, priority_pages),
            reverse=True,
        )
        budget = self.DEFAULT_BATCH_BUDGET if batch_budget is None else batch_budget
        self._start_next_batch(filepath, budget)

    def enqueue_page_scans(
        self,
        filepath: str,
        pages: list[int] | range,
        blocks: list[DocumentBlock],
        doc_hash: str = "",
        priority_pages: set[int] | None = None,
        batch_budget: int | None = None,
        drain_queue: bool = False,
        scan_round: str = FormulaScanRound.PDF_STRUCTURE.value,
    ) -> int:
        """Queue page-level MFD scans that can discover image/scanned formulas."""
        if not filepath or not pages:
            return 0
        valid_pages: list[int] = []
        for page in pages:
            try:
                page_num = int(page)
            except (TypeError, ValueError):
                continue
            if page_num >= 0:
                valid_pages.append(page_num)
        page_nums = sorted(set(valid_pages))
        if not page_nums:
            return 0
        priority_pages = priority_pages or set()
        self._active_doc_hash = doc_hash
        self._drain_page_queue = drain_queue
        self._page_scan_round = scan_round
        queued = 0
        if doc_hash:
            queued = self._store.enqueue_pages(
                doc_hash,
                filepath,
                page_nums,
                priority_pages,
                scan_round=scan_round,
            )
            if queued:
                _logger.info("页面级公式检测任务入队: doc=%s pages=%d", doc_hash, queued)
            eligible_pages = {
                task.page_num
                for task in self._store.list_page_tasks(
                    doc_hash,
                    statuses={"queued", "running"},
                    scan_round=scan_round,
                    limit=max(len(page_nums), 10000),
                )
            }
            page_nums = [page_num for page_num in page_nums if page_num in eligible_pages]
            if not page_nums:
                self.scan_finished.emit(0, self._store.page_pending_count(doc_hash))
                return queued
        existing_pages = set(self._queued_page_nums)
        self._queued_page_nums.extend(
            page_num for page_num in page_nums
            if page_num not in existing_pages
        )
        self._queued_page_nums.sort(
            key=lambda page_num: self._page_priority_key(page_num, priority_pages),
            reverse=True,
        )
        self._page_scan_blocks = [block.model_copy(deep=True) for block in blocks]
        budget = self.DEFAULT_PAGE_BATCH_BUDGET if batch_budget is None else batch_budget
        self._start_next_page_scan_batch(filepath, budget)
        return queued

    def start_page_scan_batch(
        self,
        filepath: str,
        doc_hash: str,
        blocks: list[DocumentBlock],
        allowed_pages: set[int] | None = None,
        priority_pages: set[int] | None = None,
        batch_budget: int | None = None,
        scan_round: str = FormulaScanRound.PDF_STRUCTURE.value,
    ) -> int:
        """Start one persisted page-scan batch without re-enqueueing the document."""
        if not filepath or not doc_hash or self.is_running:
            return 0
        priority_pages = priority_pages or set()
        allowed_pages = set(allowed_pages or set())
        eligible_pages = {
            task.page_num
            for task in self._store.list_page_tasks(
                doc_hash,
                statuses={"queued", "running"},
                scan_round=scan_round,
                limit=10000,
            )
        }
        if not self._queued_page_nums:
            self._queued_page_nums = [
                page_num for page_num in sorted(eligible_pages)
                if not allowed_pages or page_num in allowed_pages
            ]
        else:
            self._queued_page_nums = [
                page_num for page_num in self._queued_page_nums
                if page_num in eligible_pages and (not allowed_pages or page_num in allowed_pages)
            ]
        if not self._queued_page_nums:
            return 0
        self._queued_page_nums.sort(
            key=lambda page_num: self._page_priority_key(page_num, priority_pages),
            reverse=True,
        )
        self._active_doc_hash = doc_hash
        self._drain_page_queue = False
        self._page_scan_round = scan_round
        self._page_scan_blocks = [block.model_copy(deep=True) for block in blocks]
        budget = self.DEFAULT_PAGE_BATCH_BUDGET if batch_budget is None else batch_budget
        starting = min(max(0, int(budget)), len(self._queued_page_nums))
        self._start_next_page_scan_batch(filepath, budget)
        return starting

    def enqueue_plan(
        self,
        filepath: str,
        doc_hash: str,
        plan: FormulaScanPlan,
    ) -> None:
        """Enqueue a scheduler-produced scan plan."""
        self.enqueue_blocks(
            filepath=filepath,
            blocks=plan.blocks,
            doc_hash=doc_hash,
            priority_pages=plan.priority_pages,
            batch_budget=plan.batch_budget,
            drain_queue=plan.drain_queue,
            cache_only=plan.cache_only,
            scan_round=plan.scan_round,
        )

    def pending_count(self, doc_hash: str) -> int:
        return self._store.pending_count(doc_hash)

    def page_pending_count(self, doc_hash: str) -> int:
        return self._store.page_pending_count(doc_hash)

    def round_pending_count(
        self,
        doc_hash: str,
        scan_round: str | FormulaScanRound = FormulaScanRound.LOCAL_HIGH_PRECISION,
    ) -> int:
        return self._store.round_pending_count(doc_hash, scan_round=scan_round)

    def enqueue_semantic_review_blocks(
        self,
        filepath: str,
        doc_hash: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
    ) -> int:
        """Queue non-OCR formula blocks for later semantic/cloud review."""
        formula_blocks = [
            block.model_copy(deep=True)
            for block in blocks
            if block.block_type == BlockType.FORMULA
        ]
        payloads = {
            block.id: {
                "stage": FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
                "input_hash": FormulaIndexStore.content_hash(block),
                "content_hash": FormulaIndexStore.content_hash(block),
                "model": "pending_semantic_review",
                "model_version": "pending_semantic_review",
            }
            for block in formula_blocks
        }
        return self._store.enqueue_round_records(
            doc_hash,
            filepath,
            FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
            "block",
            formula_blocks,
            priority_pages=priority_pages,
            result_json_by_target=payloads,
        )

    def stop(self) -> None:
        """Stop the active worker if one is running."""
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.quit()
            self._thread.wait(1500)
        if self._page_thread and self._page_thread.isRunning():
            self._page_thread.requestInterruption()
            self._page_thread.quit()
            self._page_thread.wait(1500)
        self._thread = None
        self._page_thread = None
        self._queued_blocks.clear()
        self._queued_page_nums.clear()
        self._page_scan_blocks.clear()

    def _start_next_batch(self, filepath: str, batch_budget: int) -> None:
        if self.is_running:
            return
        batch_budget = max(0, int(batch_budget))
        if batch_budget <= 0 or not self._queued_blocks:
            self.scan_finished.emit(0, len(self._queued_blocks))
            return
        batch = self._queued_blocks[:batch_budget]
        self._queued_blocks = self._queued_blocks[batch_budget:]
        if self._active_doc_hash:
            self._store.mark_running(
                self._active_doc_hash,
                [block.id for block in batch],
                scan_round=self._scan_round,
            )
        self._thread = _FormulaOcrWorker(
            filepath,
            batch,
            doc_hash=self._active_doc_hash,
            cache_only=self._cache_only,
            scan_round=self._scan_round,
        )
        self._thread.finished_signal.connect(
            lambda result, fp=filepath, budget=batch_budget:
                self._on_worker_finished(result, fp, budget)
        )
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(
            lambda fp=filepath, budget=batch_budget: self._on_worker_thread_done(fp, budget)
        )
        self._thread.start()

    def _start_next_available_batch(self, filepath: str, batch_budget: int) -> None:
        if self.is_running:
            return
        if self._queued_blocks:
            self._start_next_batch(filepath, batch_budget)
            return
        if self._queued_page_nums:
            self._start_next_page_scan_batch(filepath, batch_budget)

    def _start_next_page_scan_batch(self, filepath: str, batch_budget: int) -> None:
        if self.is_running:
            return
        batch_budget = max(0, int(batch_budget))
        if batch_budget <= 0 or not self._queued_page_nums:
            self.scan_finished.emit(0, len(self._queued_blocks) + len(self._queued_page_nums))
            return
        batch = self._queued_page_nums[:batch_budget]
        self._queued_page_nums = self._queued_page_nums[batch_budget:]
        if self._active_doc_hash:
            self._store.mark_pages_running(
                self._active_doc_hash,
                batch,
                scan_round=self._page_scan_round,
            )
        self._page_thread = _FormulaPageScanWorker(
            filepath,
            batch,
            self._page_scan_blocks,
            doc_hash=self._active_doc_hash,
            scan_round=self._page_scan_round,
        )
        self._page_thread.finished_signal.connect(
            lambda result, fp=filepath, budget=batch_budget:
                self._on_page_scan_finished(result, fp, budget)
        )
        self._page_thread.finished.connect(self._page_thread.deleteLater)
        self._page_thread.finished.connect(
            lambda fp=filepath, budget=batch_budget:
                self._on_page_scan_thread_done(fp, budget)
        )
        self._page_thread.start()

    def _on_worker_finished(
        self,
        result: dict[str, object],
        filepath: str,
        batch_budget: int,
    ) -> None:
        updated = list(result.get("updated", []))
        pending = int(result.get("pending", 0) or 0)
        doc_hash = str(result.get("doc_hash", "") or "")
        for item in result.get("done", []):
            if not isinstance(item, dict):
                continue
            scan_round = str(item.get("scan_round", self._scan_round) or self._scan_round)
            block_id = str(item.get("block_id", ""))
            image_hash = str(item.get("image_hash", "") or "")
            if doc_hash and block_id and image_hash:
                self._store.put_recognition_result(
                    doc_hash=doc_hash,
                    candidate_id=block_id,
                    stage=self._stage_for_round(scan_round),
                    model=str(item.get("model", "pix2text-mfr") or "pix2text-mfr"),
                    model_version=str(item.get("model_version", "") or ""),
                    preprocess_version=str(item.get("preprocess_version", "") or ""),
                    input_hash=image_hash,
                    latex=str(item.get("latex", "") or ""),
                    normalized_latex=str(item.get("normalized_latex", item.get("latex", "")) or ""),
                    score=self._optional_float(item.get("score")),
                    duration_ms=int(item.get("duration_ms", 0) or 0),
                    peak_memory_mb=self._optional_float(item.get("peak_memory_mb")),
                    warnings=self._string_list(item.get("warnings")),
                    evidence={
                        "scan_round": scan_round,
                        "source": "formula_index_worker",
                        "block_id": block_id,
                    },
                    accepted=scan_round == FormulaScanRound.CACHED_RECOGNITION.value,
                )
            self._store.mark_done(
                doc_hash,
                block_id,
                str(item.get("latex", "")),
                image_hash,
                str(item.get("model", "pix2text-mfr") or "pix2text-mfr"),
                scan_round=scan_round,
                model_version=str(item.get("model_version", "") or ""),
                preprocess_version=str(item.get("preprocess_version", "") or ""),
                score=self._optional_float(item.get("score")),
                warnings=self._string_list(item.get("warnings")),
            )
        for item in result.get("skipped", []):
            if not isinstance(item, dict):
                continue
            self._store.mark_skipped(
                doc_hash,
                str(item.get("block_id", "")),
                str(item.get("reason", "skipped") or "skipped"),
                scan_round=str(item.get("scan_round", self._scan_round) or self._scan_round),
            )
        for item in result.get("failed", []):
            if not isinstance(item, dict):
                continue
            self._store.mark_failed(
                doc_hash,
                str(item.get("block_id", "")),
                str(item.get("error", "failed") or "failed"),
                scan_round=str(item.get("scan_round", self._scan_round) or self._scan_round),
            )
        recognized = len(updated)
        if updated:
            self.formulas_updated.emit(updated)
        total_pending = pending + len(self._queued_blocks)
        self.scan_finished.emit(recognized, total_pending)

    def _on_page_scan_finished(
        self,
        result: dict[str, object],
        filepath: str,
        batch_budget: int,
    ) -> None:
        doc_hash = str(result.get("doc_hash", "") or "")
        scan_round = str(result.get("scan_round", self._page_scan_round) or self._page_scan_round)
        done_pages = [
            int(page)
            for page in result.get("done_pages", [])
            if isinstance(page, int)
        ]
        if done_pages:
            self._store.mark_pages_done(doc_hash, done_pages, scan_round=scan_round)
        for item in result.get("failed", []):
            if not isinstance(item, dict):
                continue
            try:
                page_num = int(item.get("page_num", -1))
            except (TypeError, ValueError):
                continue
            if page_num >= 0:
                self._store.mark_page_failed(
                    doc_hash,
                    page_num,
                    str(item.get("error", "failed") or "failed"),
                    scan_round=str(item.get("scan_round", scan_round) or scan_round),
                )
        detected = [
            item for item in result.get("detected", [])
            if isinstance(item, dict)
        ]
        for item in result.get("structure_candidates", []):
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id", "") or "")
            input_hash = str(item.get("input_hash", "") or "")
            if not doc_hash or not candidate_id or not input_hash:
                continue
            try:
                candidate_block = DocumentBlock(
                    id=candidate_id,
                    page_num=int(item.get("page_num", -1) or -1),
                    block_type=BlockType.FORMULA,
                    content=str(item.get("text", "") or ""),
                    bbox=tuple(float(value) for value in item.get("bbox", (0, 0, 0, 0))),  # type: ignore[union-attr]
                    metadata={
                        "needs_ocr": True,
                        "source": "born_digital_r0_review_candidate",
                        "review_trigger": "low_confidence_born_digital_structure",
                        "formula_score": self._optional_float(item.get("score")) or 0.0,
                    },
                )
            except Exception:
                candidate_block = None
            if candidate_block is not None:
                self._store.enqueue_round_records(
                    doc_hash,
                    filepath,
                    scan_round,
                    "block",
                    [candidate_block],
                )
                self._persist_symbol_identity_round(doc_hash, filepath, candidate_block, item)
            self._store.put_recognition_result(
                doc_hash=doc_hash,
                candidate_id=candidate_id,
                stage="pdf_structure",
                model=str(item.get("model", "pymupdf_born_digital_structure") or "pymupdf_born_digital_structure"),
                model_version=str(item.get("model_version", "pymupdf_rawdict_facts_v1") or "pymupdf_rawdict_facts_v1"),
                preprocess_version=str(item.get("preprocess_version", "glyph-vector-json-v1") or "glyph-vector-json-v1"),
                input_hash=input_hash,
                latex=str(item.get("latex", "") or ""),
                normalized_latex=str(item.get("latex", "") or ""),
                score=self._optional_float(item.get("score")),
                warnings=self._string_list(item.get("warnings")),
                evidence={
                    "scan_round": scan_round,
                    "source": "born_digital_pdf_structure",
                    "page_num": item.get("page_num"),
                    "bbox": item.get("bbox"),
                    "text": item.get("text"),
                    "details": item.get("evidence", {}),
                },
                accepted=False,
            )
            self._store.mark_round_done(
                doc_hash,
                scan_round,
                "block",
                candidate_id,
                {
                    "stage": "pdf_structure",
                    "latex": str(item.get("latex", "") or ""),
                    "input_hash": input_hash,
                    "model": str(item.get("model", "pymupdf_born_digital_structure") or "pymupdf_born_digital_structure"),
                    "warnings": self._string_list(item.get("warnings")),
                },
            )
            if candidate_block is not None and self._needs_local_precision_review(item):
                self._store.enqueue_blocks(
                    doc_hash,
                    filepath,
                    [candidate_block],
                    scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
                )
        if detected:
            self.formula_blocks_detected.emit(detected)
        pending = len(self._queued_blocks) + len(self._queued_page_nums)
        if doc_hash:
            pending = max(
                pending,
                self._store.pending_count(doc_hash)
                + self._store.page_pending_count(doc_hash),
            )
        self.scan_finished.emit(0, pending)

    def _persist_symbol_identity_round(
        self,
        doc_hash: str,
        filepath: str,
        candidate_block: DocumentBlock,
        item: dict[str, object],
    ) -> None:
        evidence = item.get("evidence", {})
        if not isinstance(evidence, dict):
            return
        enriched = evidence.get("enriched_glyph_graph", {})
        if not isinstance(enriched, dict):
            return
        input_hash = str(enriched.get("input_hash", "") or "")
        if not input_hash:
            return
        summary = enriched.get("summary", {})
        payload: dict[str, object] = {
            "stage": FormulaScanRound.SYMBOL_IDENTITY_REPAIR.value,
            "input_hash": input_hash,
            "raw_input_hash": str(enriched.get("raw_input_hash", "") or ""),
            "model": "symbol_identity_repair",
            "model_version": str(enriched.get("repair_version", "symbol_identity_repair_v1") or "symbol_identity_repair_v1"),
            "preprocess_version": str(enriched.get("schema_version", "enriched_glyph_graph_v1") or "enriched_glyph_graph_v1"),
            "candidate_id": candidate_block.id,
            "page_num": candidate_block.page_num,
            "summary": summary if isinstance(summary, dict) else {},
        }
        self._store.enqueue_round_records(
            doc_hash,
            filepath,
            FormulaScanRound.SYMBOL_IDENTITY_REPAIR,
            "block",
            [candidate_block],
            result_json_by_target={candidate_block.id: payload},
        )
        self._store.mark_round_done(
            doc_hash,
            FormulaScanRound.SYMBOL_IDENTITY_REPAIR,
            "block",
            candidate_block.id,
            payload,
        )
    
    def _on_worker_thread_done(self, filepath: str, batch_budget: int) -> None:
        self._thread = None
        if self._drain_queue or (self._drain_page_queue and self._queued_page_nums):
            self._start_next_available_batch(filepath, batch_budget)

    def _on_page_scan_thread_done(self, filepath: str, batch_budget: int) -> None:
        self._page_thread = None
        if self._queued_blocks or (self._drain_page_queue and self._queued_page_nums):
            self._start_next_available_batch(filepath, batch_budget)

    @staticmethod
    def _ocr_candidates(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        return [
            block.model_copy(deep=True)
            for block in blocks
            if block.block_type == BlockType.FORMULA
            and block.metadata.get("needs_ocr")
            and not block.metadata.get("mfr_recognized")
        ]

    @staticmethod
    def _priority_key(block: DocumentBlock, priority_pages: set[int]) -> tuple[int, int, float]:
        page_boost = 1 if block.page_num in priority_pages else 0
        formula_score = float(block.metadata.get("formula_score", 0.0) or 0.0)
        return (page_boost, -block.page_num, formula_score)

    @staticmethod
    def _page_priority_key(page_num: int, priority_pages: set[int]) -> tuple[int, int]:
        page_boost = 1 if page_num in priority_pages else 0
        return (page_boost, -page_num)

    @staticmethod
    def _stage_for_round(scan_round: str) -> str:
        if scan_round == FormulaScanRound.PDF_STRUCTURE.value:
            return "pdf_structure"
        if scan_round == FormulaScanRound.SYMBOL_IDENTITY_REPAIR.value:
            return "symbol_identity_repair"
        if scan_round == FormulaScanRound.LOCAL_HIGH_PRECISION.value:
            return "local_precise"
        if scan_round == FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value:
            return "cloud_semantic"
        if scan_round == FormulaScanRound.KNOWLEDGE_GRAPH.value:
            return "knowledge_graph"
        if scan_round == FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE.value:
            return "knowledge_incremental_update"
        return "local_fast"

    @classmethod
    def _needs_local_precision_review(cls, item: dict[str, object]) -> bool:
        warnings = {
            warning.lower()
            for warning in cls._string_list(item.get("warnings"))
        }
        if warnings.intersection(
            {
                "low_confidence",
                "empty_latex",
                "review",
                "review_only",
                "needs_review",
                "unknown_glyph",
                "missing_tounicode",
                "table_or_text_like_region",
                "tabular_alignment",
            }
        ):
            return True
        try:
            score = float(item.get("score", 1.0) or 0.0)
        except (TypeError, ValueError):
            score = 1.0
        return score < 0.65

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list | tuple):
            return []
        return [str(item) for item in value if str(item)]


class _FormulaOcrWorker(QThread):
    """Recognize a small batch of pending formula blocks off the UI thread."""

    finished_signal = Signal(dict)  # worker result payload

    def __init__(
        self,
        filepath: str,
        blocks: list[DocumentBlock],
        doc_hash: str = "",
        cache_only: bool = True,
        scan_round: str = FormulaScanRound.CACHED_RECOGNITION.value,
        external_tool_specs: list[ExternalFormulaToolSpec] | None = None,
    ) -> None:
        super().__init__()
        self._filepath = filepath
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._doc_hash = doc_hash
        self._cache_only = cache_only
        self._scan_round = scan_round
        self._external_tool_specs = list(external_tool_specs) if external_tool_specs is not None else None

    def run(self) -> None:
        import fitz

        try:
            from src.core.formula_detector import Pix2TextMFDDetector
            from src.core.math_ocr import MathOCR

            doc = fitz.open(self._filepath)
            images: list[bytes] = []
            image_blocks: list[DocumentBlock] = []
            image_hashes: list[str] = []
            skipped: list[dict[str, str]] = []
            failed: list[dict[str, str]] = []
            for block in self._blocks:
                if self.isInterruptionRequested():
                    break
                try:
                    image = Pix2TextMFDDetector._crop_bbox_image(
                        doc,
                        block.page_num,
                        block.bbox,
                        dpi=300,
                        pad=6.0,
                    )
                except Exception as exc:
                    _logger.debug("公式索引裁剪失败 block=%s: %s", block.id, exc)
                    image = b""
                if image:
                    images.append(image)
                    image_blocks.append(block)
                    image_hashes.append(hashlib.sha256(image).hexdigest())
                else:
                    failed.append({"block_id": block.id, "error": "crop_failed"})
            doc.close()

            if not images or self.isInterruptionRequested():
                self.finished_signal.emit({
                    "doc_hash": self._doc_hash,
                    "scan_round": self._scan_round,
                    "updated": [],
                    "pending": len(self._blocks),
                    "done": [],
                    "skipped": skipped,
                    "failed": failed,
                })
                return

            max_uncached = 0 if self._cache_only else len(images)
            latex_results = MathOCR().recognize_batch(images, max_uncached=max_uncached)
            detector = Pix2TextMFDDetector()
            updated: list[DocumentBlock] = []
            done: list[dict[str, str]] = []
            candidate_only = self._scan_round != FormulaScanRound.CACHED_RECOGNITION.value
            for block, image_hash, latex in zip(image_blocks, image_hashes, latex_results, strict=False):
                cleaned = detector._normalize_latex(latex)
                if not cleaned:
                    skipped.append({
                        "block_id": block.id,
                        "reason": "cache_miss" if self._cache_only else "ocr_empty",
                        "scan_round": self._scan_round,
                    })
                    continue
                if not candidate_only:
                    block.content = wrap_math_text(cleaned, display=True)
                    block.block_type = BlockType.FORMULA
                    block.metadata.update({
                        "formula_ocr": "pix2text-mfr",
                        "mfr_recognized": True,
                        "latex_source": "background_formula_index",
                        "needs_ocr": False,
                    })
                    updated.append(block)
                done.append({
                    "block_id": block.id,
                    "latex": cleaned,
                    "image_hash": image_hash,
                    "model": "pix2text-mfr",
                    "model_version": "pix2text-mfr",
                    "preprocess_version": "crop-dpi300-pad6",
                    "scan_round": self._scan_round,
                })
            if self._scan_round == FormulaScanRound.LOCAL_HIGH_PRECISION.value:
                done.extend(
                    self._external_tool_candidates(
                        [
                            (
                                block.id,
                                image,
                                {
                                    "pdf_path": self._filepath,
                                    "page_num": block.page_num,
                                    "bbox": list(block.bbox),
                                },
                            )
                            for block, image in zip(image_blocks, images, strict=False)
                        ],
                        image_hashes,
                    )
                )
            pending = len(self._blocks) - len(updated)
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "updated": updated,
                "pending": pending,
                "done": done,
                "skipped": skipped,
                "failed": failed,
            })
        except Exception as exc:
            _logger.warning("公式索引后台 OCR 失败: %s", exc)
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "updated": [],
                "pending": len(self._blocks),
                "done": [],
                "skipped": [],
                "failed": [
                    {
                        "block_id": block.id,
                        "error": str(exc),
                        "scan_round": self._scan_round,
                    }
                    for block in self._blocks
                ],
            })

    def _external_tool_candidates(
        self,
        images: list[tuple[str, bytes, dict[str, object]]],
        image_hashes: list[str],
    ) -> list[dict[str, object]]:
        if not images:
            return []
        hash_by_id = {
            candidate_id: image_hash
            for (candidate_id, _, _), image_hash in zip(images, image_hashes, strict=False)
        }
        started = time.perf_counter()
        try:
            candidates = ExternalFormulaToolRunner().recognize_images(
                images,
                specs=self._external_tool_specs,
            )
        except Exception as exc:
            _logger.info("外部公式工具候选生成失败: %s", exc)
            return []
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        done: list[dict[str, object]] = []
        for candidate in candidates:
            image_hash = hash_by_id.get(candidate.candidate_id, "")
            if not image_hash:
                continue
            if not candidate.latex and not candidate.warnings:
                continue
            done.append({
                "block_id": candidate.candidate_id,
                "latex": candidate.latex,
                "normalized_latex": candidate.latex,
                "image_hash": image_hash,
                "model": candidate.model,
                "model_version": candidate.model_version,
                "preprocess_version": candidate.preprocess_version,
                "score": candidate.score,
                "duration_ms": candidate.duration_ms or elapsed_ms,
                "warnings": list(candidate.warnings),
                "scan_round": self._scan_round,
            })
        return done


class _FormulaPageScanWorker(QThread):
    """Run MFD on a small page batch to discover scanned/image formulas."""

    finished_signal = Signal(dict)

    def __init__(
        self,
        filepath: str,
        page_nums: list[int],
        blocks: list[DocumentBlock],
        doc_hash: str = "",
        scan_round: str = FormulaScanRound.PDF_STRUCTURE.value,
    ) -> None:
        super().__init__()
        self._filepath = filepath
        self._page_nums = [int(page_num) for page_num in page_nums]
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._doc_hash = doc_hash
        self._scan_round = scan_round

    def run(self) -> None:
        import fitz

        done_pages: list[int] = []
        failed: list[dict[str, object]] = []
        detected: list[dict[str, object]] = []
        structure_candidates: list[dict[str, object]] = []
        try:
            from src.core.born_digital_formula_extractor import BornDigitalFormulaStructureExtractor

            doc = fitz.open(self._filepath)
            structure_extractor = BornDigitalFormulaStructureExtractor()
            detector = None
            if self._scan_round != FormulaScanRound.PDF_STRUCTURE.value:
                from src.core.formula_detector import Pix2TextMFDDetector

                detector = Pix2TextMFDDetector(
                    dpi=180,
                    max_existing_ocr_blocks=0,
                    max_scanned_ocr_blocks=0,
                    max_existing_uncached_ocr_blocks=0,
                    max_scanned_uncached_ocr_blocks=0,
                    max_mfd_pages=-1,
                )
            for page_num in self._page_nums:
                if self.isInterruptionRequested():
                    break
                if page_num < 0 or page_num >= doc.page_count:
                    failed.append({"page_num": page_num, "error": "page_out_of_range"})
                    continue
                try:
                    structure_candidates.extend(
                        self._build_structure_candidates(
                            structure_extractor.extract_page(
                                doc[page_num],
                                page_num,
                                existing_ids={block.id for block in self._blocks},
                            )
                        )
                    )
                    if detector is not None:
                        page_formulas = detector.detect_specific_pages(doc, [page_num])
                        detected.extend(
                            self._build_formula_infos(page_num, page_formulas)
                        )
                    done_pages.append(page_num)
                except Exception as exc:
                    failed.append({"page_num": page_num, "error": str(exc)})
            doc.close()
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "done_pages": done_pages,
                "failed": failed,
                "detected": detected,
                "structure_candidates": structure_candidates,
            })
        except Exception as exc:
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "scan_round": self._scan_round,
                "done_pages": done_pages,
                "failed": [
                    {"page_num": page_num, "error": str(exc)}
                    for page_num in self._page_nums
                    if page_num not in done_pages
                ],
                "detected": detected,
                "structure_candidates": structure_candidates,
            })

    @staticmethod
    def _build_structure_candidates(candidates: list[object]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for candidate in candidates:
            items.append({
                "candidate_id": getattr(candidate, "candidate_id", ""),
                "page_num": getattr(candidate, "page_num", -1),
                "bbox": getattr(candidate, "bbox", (0, 0, 0, 0)),
                "text": getattr(candidate, "text", ""),
                "latex": getattr(candidate, "latex", ""),
                "score": getattr(candidate, "confidence", None),
                "input_hash": getattr(candidate, "input_hash", ""),
                "model": getattr(candidate, "model", "pymupdf_born_digital_structure"),
                "model_version": getattr(candidate, "model_version", "pymupdf_rawdict_facts_v1"),
                "preprocess_version": getattr(candidate, "preprocess_version", "glyph-vector-json-v1"),
                "warnings": list(getattr(candidate, "warnings", ()) or ()),
                "evidence": getattr(candidate, "evidence", {}),
            })
        return items

    def _build_formula_infos(
        self,
        page_num: int,
        formulas: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        page_blocks = [
            block for block in self._blocks
            if block.page_num == page_num
        ]
        existing_ids = {block.id for block in self._blocks}
        infos: list[dict[str, object]] = []
        next_index = len(page_blocks)
        for formula in formulas:
            bbox_obj = formula.get("bbox", (0, 0, 0, 0))
            try:
                bbox = tuple(float(value) for value in bbox_obj)  # type: ignore[union-attr]
            except (TypeError, ValueError):
                continue
            matched_existing = self._matching_existing_formula(page_blocks, bbox)
            if matched_existing:
                metadata = {
                    **matched_existing.metadata,
                    "formula_detector": "pix2text-mfd",
                    "formula_score": float(formula.get("score", 0.0) or 0.0),
                }
                if not metadata.get("mfr_recognized"):
                    metadata["needs_ocr"] = True
                infos.append({
                    "id": matched_existing.id,
                    "page_num": matched_existing.page_num,
                    "block_type": BlockType.FORMULA.value,
                    "content": matched_existing.content,
                    "bbox": bbox,
                    "metadata": metadata,
                })
                continue
            block_id = f"p{page_num}_b{next_index}"
            while block_id in existing_ids:
                next_index += 1
                block_id = f"p{page_num}_b{next_index}"
            next_index += 1
            existing_ids.add(block_id)
            infos.append({
                "id": block_id,
                "page_num": page_num,
                "block_type": BlockType.FORMULA.value,
                "content": "[图片公式，等待 OCR 识别]",
                "bbox": bbox,
                "metadata": {
                    "formula_detector": "pix2text-mfd",
                    "formula_score": float(formula.get("score", 0.0) or 0.0),
                    "source": "image_or_scan",
                    "needs_ocr": True,
                    "mfd_page_scan": True,
                },
                "is_new": True,
            })
        return infos

    @staticmethod
    def _matching_existing_formula(
        page_blocks: list[DocumentBlock],
        bbox: tuple[float, float, float, float],
    ) -> DocumentBlock | None:
        fb = bbox
        for block in page_blocks:
            if block.block_type not in {BlockType.FORMULA, BlockType.PARAGRAPH}:
                continue
            bx0, by0, bx1, by1 = block.bbox
            ox, oy = max(bx0, fb[0]), max(by0, fb[1])
            ox2, oy2 = min(bx1, fb[2]), min(by1, fb[3])
            if ox >= ox2 or oy >= oy2:
                continue
            overlap = (ox2 - ox) * (oy2 - oy)
            formula_area = max((fb[2] - fb[0]) * (fb[3] - fb[1]), 1.0)
            block_area = max((bx1 - bx0) * (by1 - by0), 1.0)
            if block.block_type == BlockType.FORMULA and overlap / formula_area > 0.3:
                return block
            if block.block_type == BlockType.PARAGRAPH and overlap / block_area > 0.45:
                return block
        return None
