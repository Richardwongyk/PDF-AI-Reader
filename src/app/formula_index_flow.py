"""Asynchronous formula OCR indexing flow.

This module keeps formula recognition out of the document open/render path.
It consumes formula blocks that still need OCR, recognizes a small priority
batch, and emits updated blocks so the UI and knowledge index can be refreshed
incrementally.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal

from src.core.models import BlockType, DocumentBlock

_logger = logging.getLogger(__name__)


class FormulaIndexFlow(QObject):
    """Schedule budgeted background OCR for pending formula blocks."""

    formulas_updated = Signal(list)  # list[DocumentBlock]
    scan_finished = Signal(int, int)  # (recognized, pending)

    DEFAULT_BATCH_BUDGET = 8

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: _FormulaOcrWorker | None = None
        self._queued_blocks: list[DocumentBlock] = []
        self._drain_queue = False
        self._cache_only = True

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.isRunning())

    def enqueue_blocks(
        self,
        filepath: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
        batch_budget: int | None = None,
        drain_queue: bool = False,
        cache_only: bool = True,
    ) -> None:
        """Enqueue formula blocks that still need OCR.

        The queue is intentionally in-memory for now. The persistent part is the
        image-hash OCR cache, so a later full scheduler can safely resume work by
        re-scanning blocks and immediately hitting cache for completed formulas.
        """
        if not filepath:
            return
        self._drain_queue = drain_queue
        self._cache_only = cache_only
        candidates = [
            block.model_copy(deep=True)
            for block in blocks
            if block.block_type == BlockType.FORMULA
            and block.metadata.get("needs_ocr")
            and not block.metadata.get("mfr_recognized")
        ]
        if not candidates:
            self.scan_finished.emit(0, 0)
            return
        priority_pages = priority_pages or set()
        existing_ids = {block.id for block in self._queued_blocks}
        self._queued_blocks.extend(
            block for block in candidates
            if block.id not in existing_ids
        )
        self._queued_blocks.sort(
            key=lambda block: self._priority_key(block, priority_pages),
            reverse=True,
        )
        self._start_next_batch(filepath, batch_budget or self.DEFAULT_BATCH_BUDGET)

    def stop(self) -> None:
        """Stop the active worker if one is running."""
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.quit()
            self._thread.wait(1500)
        self._thread = None
        self._queued_blocks.clear()

    def _start_next_batch(self, filepath: str, batch_budget: int) -> None:
        if self.is_running:
            return
        batch_budget = max(0, int(batch_budget))
        if batch_budget <= 0 or not self._queued_blocks:
            self.scan_finished.emit(0, len(self._queued_blocks))
            return
        batch = self._queued_blocks[:batch_budget]
        self._queued_blocks = self._queued_blocks[batch_budget:]
        self._thread = _FormulaOcrWorker(filepath, batch, cache_only=self._cache_only)
        self._thread.finished_signal.connect(
            lambda updated, pending, fp=filepath, budget=batch_budget:
                self._on_worker_finished(updated, pending, fp, budget)
        )
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(
            lambda fp=filepath, budget=batch_budget: self._on_worker_thread_done(fp, budget)
        )
        self._thread.start()

    def _on_worker_finished(
        self,
        updated: list[DocumentBlock],
        pending: int,
        filepath: str,
        batch_budget: int,
    ) -> None:
        recognized = len(updated)
        if updated:
            self.formulas_updated.emit(updated)
        total_pending = pending + len(self._queued_blocks)
        self.scan_finished.emit(recognized, total_pending)
    
    def _on_worker_thread_done(self, filepath: str, batch_budget: int) -> None:
        self._thread = None
        if self._drain_queue and self._queued_blocks:
            self._start_next_batch(filepath, batch_budget)

    @staticmethod
    def _priority_key(block: DocumentBlock, priority_pages: set[int]) -> tuple[int, int, float]:
        page_boost = 1 if block.page_num in priority_pages else 0
        formula_score = float(block.metadata.get("formula_score", 0.0) or 0.0)
        return (page_boost, -block.page_num, formula_score)


class _FormulaOcrWorker(QThread):
    """Recognize a small batch of pending formula blocks off the UI thread."""

    finished_signal = Signal(list, int)  # (updated blocks, still pending in batch)

    def __init__(
        self,
        filepath: str,
        blocks: list[DocumentBlock],
        cache_only: bool = True,
    ) -> None:
        super().__init__()
        self._filepath = filepath
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._cache_only = cache_only

    def run(self) -> None:
        import fitz

        try:
            from src.core.formula_detector import Pix2TextMFDDetector
            from src.core.math_ocr import MathOCR

            doc = fitz.open(self._filepath)
            images: list[bytes] = []
            image_blocks: list[DocumentBlock] = []
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
            doc.close()

            if not images or self.isInterruptionRequested():
                self.finished_signal.emit([], len(self._blocks))
                return

            max_uncached = 0 if self._cache_only else len(images)
            latex_results = MathOCR().recognize_batch(images, max_uncached=max_uncached)
            detector = Pix2TextMFDDetector()
            updated: list[DocumentBlock] = []
            for block, latex in zip(image_blocks, latex_results, strict=False):
                cleaned = detector._normalize_latex(latex)
                if not cleaned:
                    continue
                block.content = cleaned
                block.block_type = BlockType.FORMULA
                block.metadata.update({
                    "formula_ocr": "pix2text-mfr",
                    "mfr_recognized": True,
                    "latex_source": "background_formula_index",
                    "needs_ocr": False,
                })
                updated.append(block)
            pending = len(self._blocks) - len(updated)
            self.finished_signal.emit(updated, pending)
        except Exception as exc:
            _logger.warning("公式索引后台 OCR 失败: %s", exc)
            self.finished_signal.emit([], len(self._blocks))
