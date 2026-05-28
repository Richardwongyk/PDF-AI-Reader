"""Incremental knowledge-index updates for accepted formula revisions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.graph_index_flow import StructuralGraphExtractor
from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock


class FormulaKnowledgeEngine(Protocol):
    """Minimal KnowledgeEngine surface used by r5 updates."""

    def check_exists(self, doc_hash: str) -> bool:
        ...

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        ...


@dataclass(frozen=True)
class FormulaKnowledgeUpdateResult:
    """Bounded r5 batch result."""

    done: int
    failed: int
    skipped: int
    deferred: int
    pending: int
    graph_synced: int = 0
    graph_failed: int = 0

    def to_json(self) -> dict[str, int]:
        return {
            "done": self.done,
            "failed": self.failed,
            "skipped": self.skipped,
            "deferred": self.deferred,
            "pending": self.pending,
            "graph_synced": self.graph_synced,
            "graph_failed": self.graph_failed,
        }


class FormulaKnowledgeUpdateService:
    """Consume r5 records and upsert accepted formula blocks into the KB."""

    def __init__(
        self,
        store: FormulaIndexStore,
        knowledge_engine: FormulaKnowledgeEngine,
        graph_store: GraphIndexStore | None = None,
        graph_extractor: StructuralGraphExtractor | None = None,
        batch_size: int = 8,
    ) -> None:
        self._store = store
        self._knowledge_engine = knowledge_engine
        self._graph_store = graph_store
        self._graph_extractor = graph_extractor or (
            StructuralGraphExtractor() if graph_store is not None else None
        )
        self._batch_size = max(1, int(batch_size))

    def pending_count(self, doc_hash: str) -> int:
        return self._store.round_pending_count(
            doc_hash,
            scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        )

    def run_batch(
        self,
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> FormulaKnowledgeUpdateResult:
        if not doc_hash:
            return FormulaKnowledgeUpdateResult(0, 0, 0, 0, 0)
        batch_limit = self._batch_size if limit is None else max(0, int(limit))
        if batch_limit <= 0:
            return FormulaKnowledgeUpdateResult(0, 0, 0, 0, self.pending_count(doc_hash))
        records = self._store.list_round_records(
            doc_hash,
            statuses={"queued"},
            scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
            limit=batch_limit,
        )
        if not records:
            return FormulaKnowledgeUpdateResult(0, 0, 0, 0, self.pending_count(doc_hash))
        if not self._knowledge_engine.check_exists(doc_hash):
            return FormulaKnowledgeUpdateResult(0, 0, 0, len(records), self.pending_count(doc_hash))

        block_map = {block.id: block for block in blocks}
        self._store.mark_round_running(
            doc_hash,
            FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
            "block",
            [record.target_id for record in records],
        )
        done = 0
        failed = 0
        skipped = 0
        graph_synced = 0
        graph_failed = 0
        upsert_blocks: list[DocumentBlock] = []
        started_by_target: dict[str, float] = {}
        record_by_target = {record.target_id: record for record in records}
        for record in records:
            started_by_target[record.target_id] = time.perf_counter()
            block = block_map.get(record.target_id)
            if block is None:
                block = _block_from_record_payload(record.target_id, record.result_json, record.page_num)
            if block is None or block.block_type != BlockType.FORMULA:
                self._store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
                    "block",
                    record.target_id,
                    "missing_formula_block",
                    status="skipped",
                )
                skipped += 1
                continue
            upsert_blocks.append(_block_for_record(block, record.result_json))
        if upsert_blocks:
            try:
                self._knowledge_engine.upsert_blocks(upsert_blocks, doc_hash)
            except Exception as exc:
                for block in upsert_blocks:
                    elapsed_ms = int((time.perf_counter() - started_by_target.get(block.id, time.perf_counter())) * 1000)
                    self._store.mark_round_failed(
                        doc_hash,
                        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
                        "block",
                        block.id,
                        str(exc),
                        elapsed_ms=elapsed_ms,
                    )
                    failed += 1
            else:
                for block in upsert_blocks:
                    elapsed_ms = int((time.perf_counter() - started_by_target.get(block.id, time.perf_counter())) * 1000)
                    record = record_by_target.get(block.id)
                    record_payload = dict(record.result_json if record else {})
                    graph_result = (
                        self._sync_graph_artifact(
                            doc_hash,
                            record.filepath if record else "",
                            block,
                        )
                        if block.metadata.get("formula_r5_accepted") is True
                        else _empty_graph_sync_result()
                    )
                    if graph_result["graph_synced"]:
                        graph_synced += 1
                    if graph_result["graph_failed"]:
                        graph_failed += 1
                    self._store.mark_round_done(
                        doc_hash,
                        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
                        "block",
                        block.id,
                        {
                            **record_payload,
                            "stage": "knowledge_incremental_update",
                            "block_id": block.id,
                            "content_hash": FormulaIndexStore.content_hash(block),
                            **graph_result,
                        },
                        elapsed_ms=elapsed_ms,
                    )
                    done += 1
        return FormulaKnowledgeUpdateResult(
            done=done,
            failed=failed,
            skipped=skipped,
            deferred=0,
            pending=self.pending_count(doc_hash),
            graph_synced=graph_synced,
            graph_failed=graph_failed,
        )

    def _sync_graph_artifact(
        self,
        doc_hash: str,
        filepath: str,
        block: DocumentBlock,
    ) -> dict[str, object]:
        if self._graph_store is None or self._graph_extractor is None:
            return {
                "graph_synced": False,
                "graph_failed": False,
                "graph_error": "",
            }
        try:
            extraction = self._graph_extractor.extract(doc_hash, block)
            if not extraction.nodes and not extraction.edges:
                return {
                    "graph_synced": False,
                    "graph_failed": False,
                    "graph_error": "no_graph_facts",
                    "graph_extractor": self._graph_extractor.name,
                }
            self._graph_store.enqueue_blocks(doc_hash, filepath, [block])
            self._graph_store.mark_running(doc_hash, [block.id])
            self._graph_store.mark_done(
                doc_hash,
                block.id,
                extractor=self._graph_extractor.name,
                nodes=extraction.nodes,
                edges=extraction.edges,
            )
            return {
                "graph_synced": True,
                "graph_failed": False,
                "graph_error": "",
                "graph_extractor": self._graph_extractor.name,
                "graph_node_count": len(extraction.nodes),
                "graph_edge_count": len(extraction.edges),
                "graph_artifact_key": {
                    "doc_hash": doc_hash,
                    "block_id": block.id,
                    "extractor": self._graph_extractor.name,
                },
            }
        except Exception as exc:
            return {
                "graph_synced": False,
                "graph_failed": True,
                "graph_error": str(exc)[:500],
                "graph_extractor": self._graph_extractor.name,
            }


def _block_for_record(block: DocumentBlock, payload: dict[str, object]) -> DocumentBlock:
    accepted_latex = str(payload.get("accepted_latex", "") or "").strip()
    metadata = {
        **block.metadata,
        "formula_r5_input_hash": str(payload.get("input_hash", "") or ""),
        "formula_r5_best_result_id": str(payload.get("best_result_id", "") or ""),
        "formula_r5_fusion_version": str(payload.get("fusion_version", "") or ""),
        "formula_r5_acceptance_decision_id": str(payload.get("acceptance_decision_id", "") or ""),
        "formula_r5_acceptance_source": str(payload.get("acceptance_source", "") or ""),
    }
    if accepted_latex:
        metadata["formula_r5_accepted"] = True
        metadata["candidate_only"] = False
        return block.model_copy(
            update={
                "content": accepted_latex,
                "metadata": metadata,
            },
            deep=True,
        )
    return block.model_copy(update={"metadata": metadata}, deep=True)


def _block_from_record_payload(
    block_id: str,
    payload: dict[str, object],
    default_page_num: int,
) -> DocumentBlock | None:
    accepted_latex = str(payload.get("accepted_latex", "") or "").strip()
    if not accepted_latex:
        return None
    try:
        page_num = int(payload.get("page_num", default_page_num))
    except (TypeError, ValueError):
        page_num = default_page_num
    bbox_value = payload.get("bbox", (0, 0, 0, 0))
    if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        bbox = (0.0, 0.0, 0.0, 0.0)
    else:
        try:
            bbox = tuple(float(value) for value in bbox_value)
        except (TypeError, ValueError):
            bbox = (0.0, 0.0, 0.0, 0.0)
    return DocumentBlock(
        id=block_id,
        page_num=max(0, page_num),
        block_type=BlockType.FORMULA,
        content=accepted_latex,
        bbox=bbox,  # type: ignore[arg-type]
        metadata={
            "source": "formula_r5_payload",
            "formula_r5_payload_block": True,
            "formula_r5_candidate_id": str(payload.get("candidate_id", block_id) or block_id),
            "candidate_only": False,
        },
    )


def _empty_graph_sync_result() -> dict[str, object]:
    return {
        "graph_synced": False,
        "graph_failed": False,
        "graph_error": "",
    }
