"""Asynchronous GraphRAG indexing flow.

The flow keeps graph extraction outside the document open/render path.  The
default extractor records only structural facts that are already present in
DocumentBlock objects; richer model-backed extraction can be added behind the
same store contract later.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphExtraction:
    nodes: list[dict[str, object]]
    edges: list[dict[str, object]]


class StructuralGraphExtractor:
    """Extract graph facts from existing PDF block structure only."""

    name = "structural_v1"

    def extract(self, doc_hash: str, block: DocumentBlock) -> GraphExtraction:
        nodes: list[dict[str, object]] = []
        edges: list[dict[str, object]] = []

        doc_id = f"doc:{doc_hash}"
        page_id = f"page:{doc_hash}:{block.page_num + 1}"
        block_id = f"block:{doc_hash}:{block.id}"
        block_label = _preview(block.content, 120)

        nodes.extend([
            {"id": doc_id, "type": "document", "doc_hash": doc_hash},
            {
                "id": page_id,
                "type": "page",
                "doc_hash": doc_hash,
                "page": block.page_num + 1,
            },
            {
                "id": block_id,
                "type": "block",
                "doc_hash": doc_hash,
                "block_id": block.id,
                "block_type": block.block_type.value,
                "page": block.page_num + 1,
                "label": block_label,
            },
        ])
        edges.extend([
            {
                "source": doc_id,
                "target": page_id,
                "type": "contains_page",
                "evidence_block_id": block.id,
            },
            {
                "source": page_id,
                "target": block_id,
                "type": "contains_block",
                "evidence_block_id": block.id,
            },
        ])

        if block.section_title:
            section_id = f"section:{doc_hash}:{_stable_token(block.section_title)}"
            nodes.append({
                "id": section_id,
                "type": "section",
                "doc_hash": doc_hash,
                "title": block.section_title,
            })
            edges.append({
                "source": section_id,
                "target": block_id,
                "type": "contains_block",
                "evidence_block_id": block.id,
            })
            edges.append({
                "source": block_id,
                "target": section_id,
                "type": "in_section",
                "evidence_block_id": block.id,
            })

        if block.block_type == BlockType.HEADING:
            section_title = block.content.strip() or block.section_title
            if section_title:
                section_id = f"section:{doc_hash}:{_stable_token(section_title)}"
                nodes.append({
                    "id": section_id,
                    "type": "section",
                    "doc_hash": doc_hash,
                    "title": section_title,
                })
                edges.append({
                    "source": block_id,
                    "target": section_id,
                    "type": "defines_section",
                    "evidence_block_id": block.id,
                })

        if block.block_type == BlockType.FORMULA:
            formula_id = f"formula:{doc_hash}:{block.id}"
            nodes.append({
                "id": formula_id,
                "type": "formula",
                "doc_hash": doc_hash,
                "block_id": block.id,
                "page": block.page_num + 1,
                "latex": block.content,
                "needs_ocr": bool(block.metadata.get("needs_ocr")),
                "source": str(block.metadata.get("source", "")),
            })
            edges.append({
                "source": block_id,
                "target": formula_id,
                "type": "expresses_formula",
                "evidence_block_id": block.id,
            })

        if block.metadata.get("is_theorem"):
            theorem_id = f"theorem:{doc_hash}:{block.id}"
            nodes.append({
                "id": theorem_id,
                "type": "theorem",
                "doc_hash": doc_hash,
                "block_id": block.id,
                "label": block_label,
            })
            edges.append({
                "source": block_id,
                "target": theorem_id,
                "type": "states_theorem",
                "evidence_block_id": block.id,
            })

        return GraphExtraction(
            nodes=_dedupe_items(nodes),
            edges=_dedupe_items(edges),
        )


class GraphIndexFlow(QObject):
    """Schedule optional background GraphRAG extraction."""

    graph_finished = Signal(dict)

    def __init__(
        self,
        parent: QObject | None = None,
        store: GraphIndexStore | None = None,
        enabled: bool = False,
        batch_budget: int = 64,
    ) -> None:
        super().__init__(parent)
        self._store = store or GraphIndexStore()
        self._enabled = enabled
        self._batch_budget = max(1, int(batch_budget))
        self._thread: _GraphIndexWorker | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.isRunning())

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def enqueue_document(
        self,
        filepath: str,
        doc_hash: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
        batch_budget: int | None = None,
    ) -> bool:
        """Start one background graph extraction batch for a parsed document."""
        if not self._enabled or not filepath or not doc_hash or not blocks:
            return False
        if self.is_running:
            _logger.info("图谱索引已有后台任务运行，跳过本次调度")
            return False

        budget = self._batch_budget if batch_budget is None else max(1, int(batch_budget))
        self._thread = _GraphIndexWorker(
            store=self._store,
            filepath=filepath,
            doc_hash=doc_hash,
            blocks=blocks,
            priority_pages=priority_pages or set(),
            batch_budget=budget,
        )
        self._thread.finished_signal.connect(self._on_worker_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_worker_thread_done)
        self._thread.start()
        return True

    def pending_count(self, doc_hash: str) -> int:
        return self._store.pending_count(doc_hash)

    def stop(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.quit()
            self._thread.wait(1500)
        self._thread = None

    def close(self) -> None:
        self.stop()
        self._store.close()

    def _on_worker_finished(self, result: dict[str, object]) -> None:
        self.graph_finished.emit(result)

    def _on_worker_thread_done(self) -> None:
        self._thread = None


class _GraphIndexWorker(QThread):
    """Persist graph jobs and extract one budgeted batch off the UI thread."""

    finished_signal = Signal(dict)

    def __init__(
        self,
        store: GraphIndexStore,
        filepath: str,
        doc_hash: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int],
        batch_budget: int,
        extractor: StructuralGraphExtractor | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._filepath = filepath
        self._doc_hash = doc_hash
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._priority_pages = set(priority_pages)
        self._batch_budget = max(1, int(batch_budget))
        self._extractor = extractor or StructuralGraphExtractor()

    def run(self) -> None:
        result = run_graph_index_batch(
            store=self._store,
            filepath=self._filepath,
            doc_hash=self._doc_hash,
            blocks=self._blocks,
            priority_pages=self._priority_pages,
            batch_budget=self._batch_budget,
            extractor=self._extractor,
            interruption_requested=self.isInterruptionRequested,
        )
        self.finished_signal.emit(result)


def run_graph_index_batch(
    store: GraphIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    priority_pages: set[int] | None = None,
    batch_budget: int = 64,
    extractor: StructuralGraphExtractor | None = None,
    interruption_requested: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Persist jobs and process one graph extraction batch synchronously."""
    if not filepath or not doc_hash or not blocks:
        return {
            "doc_hash": doc_hash,
            "queued": 0,
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "pending": 0,
            "counts": {},
        }

    should_stop = interruption_requested or (lambda: False)
    extractor = extractor or StructuralGraphExtractor()
    priority_pages = priority_pages or set()
    candidates = _graph_candidates(blocks)
    queued = store.enqueue_blocks(doc_hash, filepath, candidates, priority_pages)
    if should_stop():
        return _batch_result(store, doc_hash, queued, 0, 0, 0)

    tasks = store.list_tasks(
        doc_hash,
        statuses={"queued"},
        limit=max(1, int(batch_budget)),
    )
    blocks_by_id = {block.id: block for block in candidates}
    task_ids = [task.block_id for task in tasks]
    if task_ids:
        store.mark_running(doc_hash, task_ids)

    processed = 0
    skipped = 0
    failed = 0
    for task in tasks:
        if should_stop():
            break
        block = blocks_by_id.get(task.block_id)
        if block is None:
            store.mark_skipped(doc_hash, task.block_id, "block_not_available")
            skipped += 1
            continue
        try:
            extraction = extractor.extract(doc_hash, block)
            if not extraction.nodes and not extraction.edges:
                store.mark_skipped(doc_hash, block.id, "no_structural_graph_facts")
                skipped += 1
                continue
            store.mark_done(
                doc_hash,
                block.id,
                extractor=extractor.name,
                nodes=extraction.nodes,
                edges=extraction.edges,
            )
            processed += 1
        except Exception as exc:
            store.mark_failed(doc_hash, task.block_id, str(exc))
            failed += 1

    return _batch_result(store, doc_hash, queued, processed, skipped, failed)


def _batch_result(
    store: GraphIndexStore,
    doc_hash: str,
    queued: int,
    processed: int,
    skipped: int,
    failed: int,
) -> dict[str, object]:
    counts = store.counts(doc_hash) if doc_hash else {}
    return {
        "doc_hash": doc_hash,
        "queued": queued,
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "pending": store.pending_count(doc_hash) if doc_hash else 0,
        "counts": counts,
    }


def _graph_candidates(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    candidates: list[DocumentBlock] = []
    for block in blocks:
        if block.metadata.get("shadowed_by"):
            continue
        if block.block_type in {
            BlockType.HEADING,
            BlockType.PARAGRAPH,
            BlockType.FORMULA,
            BlockType.TABLE,
        } and str(block.content or "").strip():
            candidates.append(block.model_copy(deep=True))
    return candidates


def _preview(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def _stable_token(value: str) -> str:
    import hashlib

    normalized = " ".join(str(value or "").split()).lower()
    digest = hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()
    return digest[:16]


def _dedupe_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    deduped: list[dict[str, object]] = []
    for item in items:
        key = repr(sorted(item.items(), key=lambda pair: pair[0]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
