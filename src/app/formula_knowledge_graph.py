"""Formula-specific r4 knowledge-graph updates.

This service makes ``r4_knowledge_graph`` visible in the formula round queue
instead of hiding it behind the generic GraphRAG worker.  It still writes the
actual graph artifact through ``GraphIndexStore`` so the graph backend remains
shared and replaceable.
"""

from __future__ import annotations

from dataclasses import dataclass
import time

from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.graph_index_flow import StructuralGraphExtractor
from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock


@dataclass(frozen=True)
class FormulaKnowledgeGraphResult:
    """Bounded r4 batch result."""

    queued: int
    done: int
    failed: int
    skipped: int
    pending: int

    def to_json(self) -> dict[str, int]:
        return {
            "queued": self.queued,
            "done": self.done,
            "failed": self.failed,
            "skipped": self.skipped,
            "pending": self.pending,
        }


class FormulaKnowledgeGraphService:
    """Consume r4 formula jobs and persist graph artifacts with evidence."""

    def __init__(
        self,
        formula_store: FormulaIndexStore,
        graph_store: GraphIndexStore | None = None,
        extractor: StructuralGraphExtractor | None = None,
        batch_size: int = 16,
    ) -> None:
        self._formula_store = formula_store
        self._graph_store = graph_store or GraphIndexStore()
        self._extractor = extractor or StructuralGraphExtractor()
        self._batch_size = max(1, int(batch_size))

    def pending_count(self, doc_hash: str) -> int:
        return self._formula_store.round_pending_count(
            doc_hash,
            scan_round=FormulaScanRound.KNOWLEDGE_GRAPH,
        )

    def enqueue_formula_blocks(
        self,
        filepath: str,
        doc_hash: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
    ) -> int:
        """Queue formula blocks for r4 with content-hash based skip evidence."""
        formula_blocks = [
            block.model_copy(deep=True)
            for block in blocks
            if block.block_type == BlockType.FORMULA and str(block.content or "").strip()
        ]
        payloads = {
            block.id: {
                "stage": FormulaScanRound.KNOWLEDGE_GRAPH.value,
                "input_hash": FormulaIndexStore.content_hash(block),
                "content_hash": FormulaIndexStore.content_hash(block),
                "extractor": self._extractor.name,
                "model_version": self._extractor.name,
            }
            for block in formula_blocks
        }
        return self._formula_store.enqueue_round_records(
            doc_hash,
            filepath,
            FormulaScanRound.KNOWLEDGE_GRAPH,
            "block",
            formula_blocks,
            priority_pages=priority_pages,
            result_json_by_target=payloads,
        )

    def enqueue_fusion_candidates(
        self,
        filepath: str,
        doc_hash: str,
        candidate_blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
    ) -> int:
        """Queue auditable formula candidates for r4 without accepting them."""
        formula_blocks = [
            block.model_copy(deep=True)
            for block in candidate_blocks
            if block.block_type == BlockType.FORMULA and str(block.content or "").strip()
        ]
        payloads = {
            block.id: {
                "stage": FormulaScanRound.KNOWLEDGE_GRAPH.value,
                "input_hash": str(block.metadata.get("fusion_input_hash", ""))
                or FormulaIndexStore.content_hash(block),
                "content_hash": FormulaIndexStore.content_hash(block),
                "extractor": self._extractor.name,
                "model_version": self._extractor.name,
                "candidate_only": bool(block.metadata.get("candidate_only", True)),
                "fusion_decision": str(block.metadata.get("fusion_decision", "")),
            }
            for block in formula_blocks
        }
        return self._formula_store.enqueue_round_records(
            doc_hash,
            filepath,
            FormulaScanRound.KNOWLEDGE_GRAPH,
            "block",
            formula_blocks,
            priority_pages=priority_pages,
            result_json_by_target=payloads,
        )

    def run_batch(
        self,
        doc_hash: str,
        filepath: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> FormulaKnowledgeGraphResult:
        if not doc_hash:
            return FormulaKnowledgeGraphResult(0, 0, 0, 0, 0)
        batch_limit = self._batch_size if limit is None else max(0, int(limit))
        if batch_limit <= 0:
            return FormulaKnowledgeGraphResult(0, 0, 0, 0, self.pending_count(doc_hash))

        records = self._formula_store.list_round_records(
            doc_hash,
            statuses={"queued"},
            scan_round=FormulaScanRound.KNOWLEDGE_GRAPH,
            limit=batch_limit,
        )
        if not records:
            return FormulaKnowledgeGraphResult(0, 0, 0, 0, self.pending_count(doc_hash))

        block_map = {block.id: block for block in blocks}
        self._formula_store.mark_round_running(
            doc_hash,
            FormulaScanRound.KNOWLEDGE_GRAPH,
            "block",
            [record.target_id for record in records],
        )

        done = 0
        failed = 0
        skipped = 0
        for record in records:
            started = time.perf_counter()
            block = block_map.get(record.target_id)
            if block is None or block.block_type != BlockType.FORMULA:
                self._formula_store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.KNOWLEDGE_GRAPH,
                    "block",
                    record.target_id,
                    "missing_formula_block",
                    status="skipped",
                )
                skipped += 1
                continue
            try:
                extraction = self._extractor.extract(doc_hash, block)
                if not extraction.nodes and not extraction.edges:
                    self._formula_store.mark_round_failed(
                        doc_hash,
                        FormulaScanRound.KNOWLEDGE_GRAPH,
                        "block",
                        record.target_id,
                        "no_graph_facts",
                        status="skipped",
                    )
                    skipped += 1
                    continue
                self._graph_store.enqueue_blocks(doc_hash, filepath, [block])
                self._graph_store.mark_running(doc_hash, [block.id])
                self._graph_store.mark_done(
                    doc_hash,
                    block.id,
                    extractor=self._extractor.name,
                    nodes=extraction.nodes,
                    edges=extraction.edges,
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                content_hash = FormulaIndexStore.content_hash(block)
                self._formula_store.mark_round_done(
                    doc_hash,
                    FormulaScanRound.KNOWLEDGE_GRAPH,
                    "block",
                    block.id,
                    {
                        "stage": FormulaScanRound.KNOWLEDGE_GRAPH.value,
                        "input_hash": content_hash,
                        "content_hash": content_hash,
                        "extractor": self._extractor.name,
                        "model": self._extractor.name,
                        "model_version": self._extractor.name,
                        "node_count": len(extraction.nodes),
                        "edge_count": len(extraction.edges),
                        "graph_store": "GraphIndexStore",
                        "candidate_only": bool(block.metadata.get("candidate_only")),
                        "fusion_decision": str(block.metadata.get("fusion_decision", "")),
                        "artifact_key": {
                            "doc_hash": doc_hash,
                            "block_id": block.id,
                            "extractor": self._extractor.name,
                        },
                    },
                    elapsed_ms=elapsed_ms,
                )
                done += 1
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._formula_store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.KNOWLEDGE_GRAPH,
                    "block",
                    record.target_id,
                    str(exc),
                    elapsed_ms=elapsed_ms,
                )
                failed += 1
        return FormulaKnowledgeGraphResult(
            queued=0,
            done=done,
            failed=failed,
            skipped=skipped,
            pending=self.pending_count(doc_hash),
        )
