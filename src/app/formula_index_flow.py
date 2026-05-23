"""Asynchronous formula OCR indexing flow.

This module keeps formula recognition out of the document open/render path.
It consumes formula blocks that still need OCR, recognizes a small priority
batch, and emits updated blocks so the UI and knowledge index can be refreshed
incrementally.
"""

from __future__ import annotations

import logging
import hashlib

from PySide6.QtCore import QObject, QThread, Signal

from src.app.formula_index_scheduler import FormulaScanPlan
from src.app.formula_index_store import FormulaIndexStore
from src.core.models import BlockType, DocumentBlock

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
        self._store = store or FormulaIndexStore()
        self._active_doc_hash = ""

    @property
    def is_running(self) -> bool:
        return bool(
            (self._thread and self._thread.isRunning())
            or (self._page_thread and self._page_thread.isRunning())
        )

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
        candidates = self._ocr_candidates(blocks)
        if not candidates:
            self.scan_finished.emit(0, 0)
            return
        priority_pages = priority_pages or set()
        if doc_hash:
            queued = self._store.enqueue_blocks(doc_hash, filepath, candidates, priority_pages)
            if queued:
                _logger.info("公式索引任务入队: doc=%s count=%d", doc_hash, queued)
            eligible_ids = {
                task.block_id
                for task in self._store.list_tasks(
                    doc_hash,
                    statuses={"queued", "running"},
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
        queued = 0
        if doc_hash:
            queued = self._store.enqueue_pages(doc_hash, filepath, page_nums, priority_pages)
            if queued:
                _logger.info("页面级公式检测任务入队: doc=%s pages=%d", doc_hash, queued)
            eligible_pages = {
                task.page_num
                for task in self._store.list_page_tasks(
                    doc_hash,
                    statuses={"queued", "running"},
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
        )

    def pending_count(self, doc_hash: str) -> int:
        return self._store.pending_count(doc_hash)

    def page_pending_count(self, doc_hash: str) -> int:
        return self._store.page_pending_count(doc_hash)

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
            self._store.mark_running(self._active_doc_hash, [block.id for block in batch])
        self._thread = _FormulaOcrWorker(
            filepath,
            batch,
            doc_hash=self._active_doc_hash,
            cache_only=self._cache_only,
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
            self._store.mark_pages_running(self._active_doc_hash, batch)
        self._page_thread = _FormulaPageScanWorker(
            filepath,
            batch,
            self._page_scan_blocks,
            doc_hash=self._active_doc_hash,
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
            self._store.mark_done(
                doc_hash,
                str(item.get("block_id", "")),
                str(item.get("latex", "")),
                str(item.get("image_hash", "")),
                str(item.get("model", "pix2text-mfr") or "pix2text-mfr"),
            )
        for item in result.get("skipped", []):
            if not isinstance(item, dict):
                continue
            self._store.mark_skipped(
                doc_hash,
                str(item.get("block_id", "")),
                str(item.get("reason", "skipped") or "skipped"),
            )
        for item in result.get("failed", []):
            if not isinstance(item, dict):
                continue
            self._store.mark_failed(
                doc_hash,
                str(item.get("block_id", "")),
                str(item.get("error", "failed") or "failed"),
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
        done_pages = [
            int(page)
            for page in result.get("done_pages", [])
            if isinstance(page, int)
        ]
        if done_pages:
            self._store.mark_pages_done(doc_hash, done_pages)
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
                )
        detected = [
            item for item in result.get("detected", [])
            if isinstance(item, dict)
        ]
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
    
    def _on_worker_thread_done(self, filepath: str, batch_budget: int) -> None:
        self._thread = None
        if self._drain_queue and self._queued_blocks:
            self._start_next_batch(filepath, batch_budget)

    def _on_page_scan_thread_done(self, filepath: str, batch_budget: int) -> None:
        self._page_thread = None
        if self._drain_page_queue and self._queued_page_nums:
            self._start_next_page_scan_batch(filepath, batch_budget)

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


class _FormulaOcrWorker(QThread):
    """Recognize a small batch of pending formula blocks off the UI thread."""

    finished_signal = Signal(dict)  # worker result payload

    def __init__(
        self,
        filepath: str,
        blocks: list[DocumentBlock],
        doc_hash: str = "",
        cache_only: bool = True,
    ) -> None:
        super().__init__()
        self._filepath = filepath
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._doc_hash = doc_hash
        self._cache_only = cache_only

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
            for block, image_hash, latex in zip(image_blocks, image_hashes, latex_results, strict=False):
                cleaned = detector._normalize_latex(latex)
                if not cleaned:
                    skipped.append({
                        "block_id": block.id,
                        "reason": "cache_miss" if self._cache_only else "ocr_empty",
                    })
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
                done.append({
                    "block_id": block.id,
                    "latex": cleaned,
                    "image_hash": image_hash,
                    "model": "pix2text-mfr",
                })
            pending = len(self._blocks) - len(updated)
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
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
                "updated": [],
                "pending": len(self._blocks),
                "done": [],
                "skipped": [],
                "failed": [{"block_id": block.id, "error": str(exc)} for block in self._blocks],
            })


class _FormulaPageScanWorker(QThread):
    """Run MFD on a small page batch to discover scanned/image formulas."""

    finished_signal = Signal(dict)

    def __init__(
        self,
        filepath: str,
        page_nums: list[int],
        blocks: list[DocumentBlock],
        doc_hash: str = "",
    ) -> None:
        super().__init__()
        self._filepath = filepath
        self._page_nums = [int(page_num) for page_num in page_nums]
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._doc_hash = doc_hash

    def run(self) -> None:
        import fitz

        done_pages: list[int] = []
        failed: list[dict[str, object]] = []
        detected: list[dict[str, object]] = []
        try:
            from src.core.formula_detector import Pix2TextMFDDetector

            doc = fitz.open(self._filepath)
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
                "done_pages": done_pages,
                "failed": failed,
                "detected": detected,
            })
        except Exception as exc:
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "done_pages": done_pages,
                "failed": [
                    {"page_num": page_num, "error": str(exc)}
                    for page_num in self._page_nums
                    if page_num not in done_pages
                ],
                "detected": detected,
            })

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
