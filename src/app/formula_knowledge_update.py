"""Incremental knowledge-index updates for accepted formula revisions."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
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

    def to_json(self) -> dict[str, int]:
        return {
            "done": self.done,
            "failed": self.failed,
            "skipped": self.skipped,
            "deferred": self.deferred,
            "pending": self.pending,
        }


class FormulaKnowledgeUpdateService:
    """Consume r5 records and upsert accepted formula blocks into the KB."""

    def __init__(
        self,
        store: FormulaIndexStore,
        knowledge_engine: FormulaKnowledgeEngine,
        batch_size: int = 8,
    ) -> None:
        self._store = store
        self._knowledge_engine = knowledge_engine
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
        upsert_blocks: list[DocumentBlock] = []
        started_by_target: dict[str, float] = {}
        for record in records:
            started_by_target[record.target_id] = time.perf_counter()
            block = block_map.get(record.target_id)
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
                    record_payload = next(
                        (
                            record.result_json
                            for record in records
                            if record.target_id == block.id
                        ),
                        {},
                    )
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
        )


def _block_for_record(block: DocumentBlock, payload: dict[str, object]) -> DocumentBlock:
    accepted_latex = str(payload.get("accepted_latex", "") or "").strip()
    metadata = {
        **block.metadata,
        "formula_r5_input_hash": str(payload.get("input_hash", "") or ""),
        "formula_r5_best_result_id": str(payload.get("best_result_id", "") or ""),
        "formula_r5_fusion_version": str(payload.get("fusion_version", "") or ""),
    }
    if accepted_latex:
        metadata["formula_r5_accepted"] = True
        return block.model_copy(
            update={
                "content": accepted_latex,
                "metadata": metadata,
            },
            deep=True,
        )
    return block.model_copy(update={"metadata": metadata}, deep=True)
