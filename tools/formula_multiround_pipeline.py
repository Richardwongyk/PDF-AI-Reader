"""Run an auditable r0-r4 formula parsing pipeline on bundled PDFs.

This command is a production-oriented smoke/benchmark runner.  It executes the
same persisted stores used by the app and emits a report with per-round status,
timing, skips/failures, and candidate counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.formula_index_flow import FormulaIndexFlow, _FormulaPageScanWorker, _FormulaOcrWorker
from src.app.formula_index_scheduler import FormulaIndexScheduler, FormulaScanTrigger
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.formula_knowledge_graph import FormulaKnowledgeGraphService
from src.app.formula_knowledge_update import FormulaKnowledgeUpdateService
from src.app.formula_semantic_review import FormulaSemanticReviewService
from src.app.tinybdmath_candidate_service import TINYBDMATH_PREPROCESS_VERSION, TinyBDMathCandidateService
from src.app.graph_index_store import GraphIndexStore
from src.core.ai_engine import BaseLLMClient, LiteLLMClient
from src.core.external_formula_tools import ExternalFormulaToolRunner, ExternalFormulaToolSpec
from src.core.model_providers import normalize_litellm_model
from src.core.models import BlockType, DocumentBlock
from src.core.pdf_engine import DocumentChunker
from src.data.config_manager import ConfigManager
from src.infra.file_hash import compute_sha256
from src.main import _is_configured_api_key
from tools.formula_latex_audit import (
    _best_formula_matches,
    _cases,
    _extract_source_formulas_detailed,
    _formula_similarity,
    _inline_formula_snippets_from_text,
    _normalize_formula_for_match,
)


FUSION_VERSION = "formula_candidate_fusion_v1"


@dataclass
class RoundReport:
    round: str
    status: str
    elapsed_sec: float
    counts: dict[str, int]
    details: dict[str, object]


@dataclass
class MultiRoundPipelineReport:
    case: str
    pdf: str
    doc_hash: str
    pages_scanned: int
    blocks: int
    formula_blocks: int
    status: str
    elapsed_sec: float
    rounds: list[RoundReport]
    formula_round_jobs: dict[str, int]
    formula_jobs: dict[str, int]
    page_jobs: dict[str, int]
    formula_fusion_jobs: dict[str, int]
    graph_jobs: dict[str, int]
    recognition_results: dict[str, int]
    formula_acceptance_decisions: dict[str, int]
    formula_accuracy: dict[str, object]
    formula_fusion_snapshots: list[dict[str, object]]
    formula_fusion: dict[str, object]


class _MockReviewClient(BaseLLMClient):
    """Deterministic r3 review client for default smoke runs."""

    @property
    def model_name(self) -> str:
        return "mock-formula-review"

    def generate(self, messages: list[dict[str, str]], **kwargs: object) -> str:
        return json.dumps(
            {
                "suggested_latex": "",
                "should_replace": False,
                "confidence": 0.0,
                "reason": "mock review; real cloud review is opt-in",
                "risks": ["mock_response"],
            },
            ensure_ascii=False,
        )

    def generate_stream(self, messages: list[dict[str, str]], **kwargs: object):
        yield self.generate(messages, **kwargs)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def check_availability(self) -> bool:
        return True


class _PipelineKnowledgeStub:
    """Synchronous r5 smoke backend; real app uses KnowledgeEngine."""

    def __init__(self, exists: bool = True) -> None:
        self._exists = exists
        self.upserted_blocks: list[DocumentBlock] = []

    def check_exists(self, doc_hash: str) -> bool:
        return self._exists

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        self.upserted_blocks.extend(block.model_copy(deep=True) for block in blocks)


def _select_cases(case_name: str) -> list[Any]:
    cases = _cases()
    if case_name == "all":
        return cases
    return [case for case in cases if case.name == case_name]


def _parse_blocks(pdf: Path, max_pages: int, start_page: int) -> tuple[int, list[DocumentBlock]]:
    chunker = DocumentChunker(
        enable_born_digital_math=True,
        enable_born_digital_semantics=False,
        enable_legacy_formula_heuristic=False,
    )
    blocks: list[DocumentBlock] = []
    doc = fitz.open(pdf)
    try:
        first_page = max(0, min(start_page, doc.page_count))
        last_page = min(doc.page_count, first_page + max_pages) if max_pages > 0 else doc.page_count
        for page_num in range(first_page, last_page):
            blocks.extend(chunker.chunk_page(doc, page_num))
        return max(0, last_page - first_page), blocks
    finally:
        doc.close()


def run_pipeline_case(
    case: Any,
    *,
    formula_db_path: Path,
    graph_db_path: Path,
    max_pages: int = 8,
    start_page: int = 0,
    r1_limit: int = 4,
    r2_limit: int = 2,
    r3_limit: int = 2,
    r4_limit: int = 16,
    r5_limit: int = 8,
    r2_sample_formulas: int = 0,
    auto_local_tools: bool = False,
    run_cloud_review: bool = False,
    run_targeted_r2_after_fusion: bool = False,
    drain_r2: bool = False,
    drain_r3: bool = False,
    drain_r4: bool = False,
    drain_r5: bool = False,
    run_tinybdmath: bool = False,
    tinybdmath_model: Path | None = None,
    tinybdmath_graph_parser_model: Path | None = None,
    tinybdmath_edge_model: Path | None = None,
) -> MultiRoundPipelineReport:
    started_total = time.perf_counter()
    pages_scanned, blocks = _parse_blocks(case.pdf, max_pages=max_pages, start_page=start_page)
    doc_hash = compute_sha256(str(case.pdf))[:16]
    formula_store = FormulaIndexStore(str(formula_db_path))
    graph_store = GraphIndexStore(str(graph_db_path))
    rounds: list[RoundReport] = []
    filepath = str(case.pdf)
    formula_blocks = [block for block in blocks if block.block_type == BlockType.FORMULA]
    fusion_snapshots: list[dict[str, object]] = []

    flow = FormulaIndexFlow(store=formula_store)

    r0_report, r05_report = _run_r0(flow, formula_store, filepath, doc_hash, blocks, start_page, pages_scanned)
    rounds.append(r0_report)
    rounds.append(r05_report)
    if run_tinybdmath:
        rounds.append(
            _run_tinybdmath_r2a(
                formula_store,
                doc_hash,
                filepath=filepath,
                blocks=blocks,
                tinybdmath_model=tinybdmath_model,
                tinybdmath_graph_parser_model=tinybdmath_graph_parser_model,
                limit=r2_limit,
            )
        )
    rounds.append(_run_r1(formula_store, filepath, doc_hash, blocks, limit=r1_limit))
    rounds.append(
        _run_r2(
            formula_store,
            filepath,
            doc_hash,
            blocks,
            formula_blocks=formula_blocks,
            limit=r2_limit,
            sample_formulas=r2_sample_formulas,
            auto_local_tools=auto_local_tools,
            drain=drain_r2,
        )
    )
    formula_fusion = _formula_fusion_report(
        case,
        formula_store,
        doc_hash,
        blocks,
        formula_blocks,
        max_pages=max_pages,
        filepath=filepath,
    )
    fusion_snapshots.append(_fusion_snapshot("after_initial_r2", formula_fusion, formula_store, doc_hash))
    if run_targeted_r2_after_fusion:
        rounds.append(
            _run_r2(
                formula_store,
                filepath,
                doc_hash,
                blocks,
                formula_blocks=formula_blocks,
                limit=r2_limit,
                sample_formulas=0,
                auto_local_tools=auto_local_tools,
                drain=drain_r2,
            )
        )
        formula_fusion = _formula_fusion_report(
            case,
            formula_store,
            doc_hash,
            blocks,
            formula_blocks,
            max_pages=max_pages,
            filepath=filepath,
        )
        fusion_snapshots.append(_fusion_snapshot("after_targeted_r2", formula_fusion, formula_store, doc_hash))
    rounds.append(
        _run_r3(
            formula_store,
            filepath,
            doc_hash,
            formula_blocks,
            limit=r3_limit,
            run_cloud_review=run_cloud_review,
            drain=drain_r3,
        )
    )
    formula_fusion = _formula_fusion_report(
        case,
        formula_store,
        doc_hash,
        blocks,
        formula_blocks,
        max_pages=max_pages,
        filepath=filepath,
    )
    fusion_snapshots.append(_fusion_snapshot("after_r3", formula_fusion, formula_store, doc_hash))
    rounds.append(
        _run_r4(
            formula_store,
            graph_store,
            filepath,
            doc_hash,
            blocks,
            formula_fusion=formula_fusion,
            limit=r4_limit,
            drain=drain_r4,
        )
    )
    rounds.append(
        _run_r5(
            formula_store,
            graph_store,
            filepath,
            doc_hash,
            formula_blocks,
            limit=r5_limit,
            drain=drain_r5,
        )
    )
    recognition_results = _recognition_result_counts(formula_store, doc_hash)
    formula_accuracy = _formula_accuracy_report(
        case,
        formula_store,
        doc_hash,
        blocks,
        formula_blocks,
        max_pages=max_pages,
    )
    elapsed_sec = time.perf_counter() - started_total
    status = "ok" if all(report.status in {"done", "skipped"} for report in rounds) else "partial"
    return MultiRoundPipelineReport(
        case=case.name,
        pdf=filepath,
        doc_hash=doc_hash,
        pages_scanned=pages_scanned,
        blocks=len(blocks),
        formula_blocks=len(formula_blocks),
        status=status,
        elapsed_sec=round(elapsed_sec, 3),
        rounds=rounds,
        formula_round_jobs=formula_store.round_counts(doc_hash),
        formula_jobs=formula_store.counts(doc_hash),
        page_jobs=formula_store.page_counts(doc_hash),
        formula_fusion_jobs=formula_store.fusion_counts(doc_hash),
        graph_jobs=graph_store.counts(doc_hash),
        recognition_results=recognition_results,
        formula_acceptance_decisions=_acceptance_decision_counts(formula_store, doc_hash),
        formula_accuracy=formula_accuracy,
        formula_fusion_snapshots=fusion_snapshots,
        formula_fusion=formula_fusion,
    )


def _fusion_snapshot(
    stage: str,
    formula_fusion: dict[str, object],
    store: FormulaIndexStore,
    doc_hash: str,
) -> dict[str, object]:
    summary = formula_fusion.get("summary", {}) if isinstance(formula_fusion, dict) else {}
    persisted = summary.get("persisted", {}) if isinstance(summary, dict) else {}
    if not isinstance(persisted, dict):
        persisted = {}
    return {
        "stage": stage,
        "candidate_count": int(summary.get("candidate_count", 0) or 0) if isinstance(summary, dict) else 0,
        "ready_for_manual_accept": int(summary.get("ready_for_manual_accept", 0) or 0)
        if isinstance(summary, dict) else 0,
        "needs_more_evidence": int(summary.get("needs_more_evidence", 0) or 0)
        if isinstance(summary, dict) else 0,
        "local_precise_degraded": int(summary.get("local_precise_degraded", 0) or 0)
        if isinstance(summary, dict) else 0,
        "persisted": {
            key: int(persisted.get(key, 0) or 0)
            for key in (
                "fusion_records_upserted",
                "already_done_same_input",
                "r2_queued",
                "r3_queued",
                "r5_queued",
            )
        },
        "stored_decisions": store.fusion_counts(doc_hash),
        "acceptance_decisions": _acceptance_decision_counts(store, doc_hash),
    }


def _run_r0(
    flow: FormulaIndexFlow,
    store: FormulaIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    start_page: int,
    pages_scanned: int,
) -> tuple[RoundReport, RoundReport]:
    started = time.perf_counter()
    pages = list(range(start_page, start_page + pages_scanned))
    r05_before = store.round_counts(doc_hash, FormulaScanRound.SYMBOL_IDENTITY_REPAIR)
    queued = store.enqueue_pages(
        doc_hash,
        filepath,
        pages,
        scan_round=FormulaScanRound.PDF_STRUCTURE,
    )
    pending_tasks = store.list_page_tasks(
        doc_hash,
        statuses={"queued"},
        scan_round=FormulaScanRound.PDF_STRUCTURE,
        limit=max(1, len(pages)),
    )
    done_pages = 0
    failed = 0
    for page_num in [task.page_num for task in pending_tasks]:
        store.mark_pages_running(
            doc_hash,
            [page_num],
            scan_round=FormulaScanRound.PDF_STRUCTURE,
        )
        worker = _FormulaPageScanWorker(
            filepath,
            [page_num],
            blocks,
            doc_hash=doc_hash,
            scan_round=FormulaScanRound.PDF_STRUCTURE.value,
        )
        emitted: list[dict[str, object]] = []
        worker.finished_signal.connect(emitted.append)
        worker.run()
        if not emitted:
            failed += 1
            continue
        payload = emitted[0]
        done_pages += len(payload.get("done_pages", []) or [])
        failed += len(payload.get("failed", []) or [])
        flow._on_page_scan_finished(payload, filepath, 1)
    counts = store.round_counts(doc_hash, FormulaScanRound.PDF_STRUCTURE)
    r05_counts = store.round_counts(doc_hash, FormulaScanRound.SYMBOL_IDENTITY_REPAIR)
    r05_done_before = r05_before.get(f"{FormulaScanRound.SYMBOL_IDENTITY_REPAIR.value}:done", 0)
    r05_done_after = r05_counts.get(f"{FormulaScanRound.SYMBOL_IDENTITY_REPAIR.value}:done", 0)
    status = "done" if failed == 0 else "partial"
    elapsed = round(time.perf_counter() - started, 3)
    return (
        RoundReport(
            round=FormulaScanRound.PDF_STRUCTURE.value,
            status=status,
            elapsed_sec=elapsed,
            counts=counts,
            details={
                "queued_pages": queued,
                "processed_pages": len(pending_tasks),
                "done_pages": done_pages,
                "failed_pages": failed,
                "skipped_completed_pages": max(0, len(pages) - len(pending_tasks)),
            },
        ),
        RoundReport(
            round=FormulaScanRound.SYMBOL_IDENTITY_REPAIR.value,
            status="done" if r05_counts else "skipped",
            elapsed_sec=0.0,
            counts=r05_counts,
            details={
                "source_round": FormulaScanRound.PDF_STRUCTURE.value,
                "new_or_updated_records": max(0, r05_done_after - r05_done_before),
                "skipped_same_input": r05_done_before if not pending_tasks else 0,
                "persisted_records": r05_done_after,
            },
        ),
    )


def _run_r1(
    store: FormulaIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    limit: int,
) -> RoundReport:
    started = time.perf_counter()
    flow = FormulaIndexFlow(store=store)
    scheduler = FormulaIndexScheduler()
    plan = scheduler.plan_for_pages(
        blocks,
        pages=set(),
        trigger=FormulaScanTrigger.BACKGROUND,
        page_count=max(1, max((block.page_num for block in blocks), default=0) + 1),
    )
    queued = store.enqueue_blocks(
        doc_hash,
        filepath,
        plan.blocks,
        plan.priority_pages,
        scan_round=FormulaScanRound.CACHED_RECOGNITION,
    )
    tasks = store.list_tasks(
        doc_hash,
        statuses={"queued"},
        scan_round=FormulaScanRound.CACHED_RECOGNITION,
        limit=max(0, limit),
    )
    selected = [block for block in plan.blocks if block.id in {task.block_id for task in tasks}]
    if selected:
        worker = _FormulaOcrWorker(
            filepath,
            selected,
            doc_hash=doc_hash,
            cache_only=True,
            scan_round=FormulaScanRound.CACHED_RECOGNITION.value,
        )
        store.mark_running(
            doc_hash,
            [block.id for block in selected],
            scan_round=FormulaScanRound.CACHED_RECOGNITION,
        )
        emitted: list[dict[str, object]] = []
        worker.finished_signal.connect(emitted.append)
        worker.run()
        if emitted:
            flow._on_worker_finished(emitted[0], filepath, max(1, len(selected)))
    counts = store.round_counts(doc_hash, FormulaScanRound.CACHED_RECOGNITION)
    completed = counts.get(f"{FormulaScanRound.CACHED_RECOGNITION.value}:done", 0)
    return RoundReport(
        round=FormulaScanRound.CACHED_RECOGNITION.value,
        status="done" if queued or tasks or completed else "skipped",
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=counts,
        details={
            "queued_blocks": queued,
            "processed_cache_only": len(selected),
            "skipped_completed_blocks": completed if not queued and not selected else 0,
        },
    )


def _run_tinybdmath_r2a(
    store: FormulaIndexStore,
    doc_hash: str,
    *,
    filepath: str,
    blocks: list[DocumentBlock],
    tinybdmath_model: Path | None,
    tinybdmath_graph_parser_model: Path | None,
    limit: int,
) -> RoundReport:
    started = time.perf_counter()
    service = TinyBDMathCandidateService(
        store,
        model_path=tinybdmath_model,
        graph_parser_model_path=tinybdmath_graph_parser_model,
    )
    inline_items = _inline_formula_candidate_items(blocks)
    if int(limit) > 0:
        structure_limit = max(1, int(limit))
        inline_limit = max(1, int(limit) * 8)
    else:
        structure_limit = 100000
        inline_limit = len(inline_items)
    structure_details = service.process_doc(doc_hash, filepath=filepath, limit=structure_limit)
    inline_details = service.process_inline_candidates(
        doc_hash,
        inline_items,
        filepath=filepath,
        limit=inline_limit,
    )
    failed = int(structure_details.get("failed", 0) or 0) + int(inline_details.get("failed", 0) or 0)
    processed = int(structure_details.get("processed", 0) or 0) + int(inline_details.get("processed", 0) or 0)
    skipped_cached = int(structure_details.get("skipped_cached", 0) or 0) + int(inline_details.get("skipped_cached", 0) or 0)
    status = "done" if failed == 0 else "partial"
    if processed == 0 and skipped_cached == 0:
        status = "skipped"
    details = {
        "stage": "tinybdmath_structural",
        "model": "tinybdmath",
        "model_version": service.model_version,
        "graph_parser_model_version": service.graph_parser_model_version,
        "legacy_edge_model": {
            "enabled": False,
            "reason": "Graph Parser is the r2a main path; edge scorer is baseline-only.",
        },
        "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
        "structure": structure_details,
        "inline": inline_details,
        "records_seen": int(structure_details.get("records_seen", 0) or 0) + int(inline_details.get("records_seen", 0) or 0),
        "processed": processed,
        "skipped_cached": skipped_cached,
        "skipped_no_evidence": int(structure_details.get("skipped_no_evidence", 0) or 0)
        + int(inline_details.get("skipped_no_evidence", 0) or 0),
        "failed": failed,
        "elapsed_ms": int(structure_details.get("elapsed_ms", 0) or 0) + int(inline_details.get("elapsed_ms", 0) or 0),
    }
    return RoundReport(
        round="r2a_tinybdmath_structural",
        status=status,
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts={"tinybdmath_structural": processed},
        details=details,
    )


def _run_r2(
    store: FormulaIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    formula_blocks: list[DocumentBlock],
    limit: int,
    sample_formulas: int,
    auto_local_tools: bool,
    drain: bool = False,
) -> RoundReport:
    started = time.perf_counter()
    sampled = 0
    if sample_formulas > 0:
        candidates = [
            block.model_copy(
                update={
                    "metadata": {
                        **block.metadata,
                        "needs_ocr": True,
                        "source": block.metadata.get("source", "explicit_r2_sample"),
                        "review_trigger": "explicit_r2_pipeline_sample",
                        "r2_sample_only": True,
                    }
                },
                deep=True,
            )
            for block in formula_blocks[:sample_formulas]
        ]
        sampled = store.enqueue_blocks(
            doc_hash,
            filepath,
            candidates,
            scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
        )
    block_map = {block.id: block for block in blocks}
    external_tool_specs: list[ExternalFormulaToolSpec] | None = None
    discovered_tools: list[str] = []
    if auto_local_tools:
        external_tool_specs = ExternalFormulaToolRunner.known_local_specs()
        discovered_tools = [spec.name for spec in external_tool_specs]
    processed_blocks = 0
    batches = 0
    emitted_any = False
    while True:
        tasks = store.list_tasks(
            doc_hash,
            statuses={"queued"},
            scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
            limit=max(0, limit),
        )
        store_blocks = {
            task.block_id: DocumentBlock(
                id=task.block_id,
                page_num=task.page_num,
                block_type=BlockType.FORMULA,
                content="",
                bbox=task.bbox,
                metadata={"needs_ocr": True, "formula_score": task.priority},
            )
            for task in tasks
        }
        selected = [
            block_map.get(task.block_id) or store_blocks[task.block_id]
            for task in tasks
            if task.block_id in block_map or task.block_id in store_blocks
        ]
        if not selected:
            break
        worker = _FormulaOcrWorker(
            filepath,
            selected,
            doc_hash=doc_hash,
            cache_only=False,
            scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
            external_tool_specs=external_tool_specs,
        )
        store.mark_running(
            doc_hash,
            [block.id for block in selected],
            scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
        )
        emitted: list[dict[str, object]] = []
        worker.finished_signal.connect(emitted.append)
        worker.run()
        if emitted:
            emitted_any = True
            batches += 1
            processed_blocks += len(selected)
            FormulaIndexFlow(store=store)._on_worker_finished(emitted[0], filepath, max(1, len(selected)))
        if not drain:
            break
    counts = store.round_counts(doc_hash, FormulaScanRound.LOCAL_HIGH_PRECISION)
    pending = store.round_pending_count(doc_hash, FormulaScanRound.LOCAL_HIGH_PRECISION)
    if not emitted_any:
        return RoundReport(
            round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
            status="skipped",
            elapsed_sec=round(time.perf_counter() - started, 3),
            counts=counts,
            details={"reason": "no_pending_r2_blocks", "explicit_samples_queued": sampled},
        )
    return RoundReport(
        round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
        status="done",
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=counts,
        details={
            "processed_blocks": processed_blocks,
            "batches": batches,
            "drained": drain and pending == 0,
            "pending": pending,
            "explicit_samples_queued": sampled,
            "auto_local_tools": auto_local_tools,
            "discovered_tools": discovered_tools,
        },
    )


def _run_r3(
    store: FormulaIndexStore,
    filepath: str,
    doc_hash: str,
    formula_blocks: list[DocumentBlock],
    limit: int,
    run_cloud_review: bool,
    drain: bool = False,
) -> RoundReport:
    started = time.perf_counter()
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
    queued = store.enqueue_round_records(
        doc_hash,
        filepath,
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        formula_blocks,
        result_json_by_target=payloads,
    )
    client = _cloud_review_client() if run_cloud_review else _MockReviewClient()
    service = FormulaSemanticReviewService(store, client, batch_size=max(1, limit), timeout_sec=90)
    counts = {"done": 0, "failed": 0, "skipped": 0}
    batches = 0
    while True:
        current = service.run_batch(doc_hash, formula_blocks, limit=max(0, limit))
        if not any(int(value) for value in current.values()):
            break
        batches += 1
        for key in counts:
            counts[key] += int(current.get(key, 0) or 0)
        if not drain:
            break
        if service.pending_count(doc_hash) <= 0:
            break
    processed_total = sum(int(value) for value in counts.values())
    return RoundReport(
        round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
        status="done" if processed_total > 0 else ("queued" if queued else "skipped"),
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=store.round_counts(doc_hash, FormulaScanRound.CLOUD_SEMANTIC_REVIEW),
        details={
            "queued_reviews": queued,
            "processed": counts,
            "batches": batches,
            "drained": drain and service.pending_count(doc_hash) == 0,
            "pending": service.pending_count(doc_hash),
            "client": client.model_name,
            "cloud": run_cloud_review,
        },
    )


def _run_r4(
    formula_store: FormulaIndexStore,
    graph_store: GraphIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    formula_fusion: dict[str, object] | None,
    limit: int,
    drain: bool = False,
) -> RoundReport:
    started = time.perf_counter()
    service = FormulaKnowledgeGraphService(
        formula_store,
        graph_store,
        batch_size=max(1, limit),
    )
    queued = service.enqueue_formula_blocks(filepath, doc_hash, blocks)
    fusion_blocks = _fusion_graph_blocks(formula_fusion or {})
    queued_candidates = service.enqueue_fusion_candidates(
        filepath,
        doc_hash,
        fusion_blocks,
    ) if fusion_blocks else 0
    totals = {"queued": 0, "done": 0, "failed": 0, "skipped": 0, "pending": 0}
    batches = 0
    last_result = service.run_batch(doc_hash, filepath, blocks + fusion_blocks, limit=max(0, limit))
    if last_result.done or last_result.failed or last_result.skipped:
        batches += 1
    for key, value in last_result.to_json().items():
        totals[key] = int(value)
    while drain and service.pending_count(doc_hash) > 0:
        current = service.run_batch(doc_hash, filepath, blocks + fusion_blocks, limit=max(0, limit))
        if not (current.done or current.failed or current.skipped):
            break
        batches += 1
        totals["done"] += current.done
        totals["failed"] += current.failed
        totals["skipped"] += current.skipped
        totals["pending"] = current.pending
    result = totals
    processed_total = int(result["done"]) + int(result["failed"]) + int(result["skipped"])
    return RoundReport(
        round=FormulaScanRound.KNOWLEDGE_GRAPH.value,
        status="done" if processed_total > 0 else ("queued" if queued or int(result["pending"]) else "skipped"),
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=formula_store.round_counts(doc_hash, FormulaScanRound.KNOWLEDGE_GRAPH),
        details={
            **result,
            "queued_reviews": queued,
            "queued_formula_candidates": queued_candidates,
            "batches": batches,
            "drained": drain and service.pending_count(doc_hash) == 0,
            "graph_jobs": graph_store.counts(doc_hash),
            "extractor": "structural_v1",
        },
    )


def _run_r5(
    store: FormulaIndexStore,
    graph_store: GraphIndexStore,
    filepath: str,
    doc_hash: str,
    formula_blocks: list[DocumentBlock],
    limit: int,
    drain: bool = False,
) -> RoundReport:
    started = time.perf_counter()
    service = FormulaKnowledgeUpdateService(
        store,
        _PipelineKnowledgeStub(exists=True),
        graph_store=graph_store,
        batch_size=max(1, limit),
    )
    totals = {
        "done": 0,
        "failed": 0,
        "skipped": 0,
        "deferred": 0,
        "pending": service.pending_count(doc_hash),
        "graph_synced": 0,
        "graph_failed": 0,
    }
    batches = 0
    while True:
        result = service.run_batch(doc_hash, formula_blocks, limit=max(0, limit))
        if not (result.done or result.failed or result.skipped or result.deferred):
            totals["pending"] = result.pending
            break
        batches += 1
        totals["done"] += result.done
        totals["failed"] += result.failed
        totals["skipped"] += result.skipped
        totals["deferred"] += result.deferred
        totals["graph_synced"] += result.graph_synced
        totals["graph_failed"] += result.graph_failed
        totals["pending"] = result.pending
        if not drain or result.deferred or result.pending <= 0:
            break
    processed_total = totals["done"] + totals["failed"] + totals["skipped"]
    return RoundReport(
        round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE.value,
        status="done" if processed_total > 0 else ("queued" if totals["pending"] else "skipped"),
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=store.round_counts(doc_hash, FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE),
        details={
            **totals,
            "batches": batches,
            "drained": drain and service.pending_count(doc_hash) == 0,
            "filepath": filepath,
            "graph_jobs": graph_store.counts(doc_hash),
            "note": "Pipeline uses a synchronous KB stub; the application wires r5 to KnowledgeEngine.upsert_blocks.",
        },
    )


def _cloud_review_client() -> LiteLLMClient:
    manager = ConfigManager(str(ROOT / "config.yaml"))
    config = manager.get()
    model = normalize_litellm_model(config.model.cloud_reasoning or config.model.cloud)
    api_key = manager.get_api_key(model) or manager.get_api_key(config.model.cloud)
    if not _is_configured_api_key(api_key):
        raise RuntimeError("configured cloud review API key is missing")
    return LiteLLMClient(model=model, api_key=api_key or "")


def _recognition_result_counts(store: FormulaIndexStore, doc_hash: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in store.list_recognition_results(doc_hash, limit=10000):
        key = f"{record.stage}:{record.model}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _acceptance_decision_counts(store: FormulaIndexStore, doc_hash: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in store.list_acceptance_decisions(doc_hash, limit=10000):
        action_key = decision.action
        source_key = f"{decision.action}:{decision.decision_source or 'unspecified'}"
        counts[action_key] = counts.get(action_key, 0) + 1
        counts[source_key] = counts.get(source_key, 0) + 1
    return counts


def _inline_formula_candidates_from_blocks(blocks: list[DocumentBlock]) -> list[str]:
    candidates: list[str] = []
    for item in _inline_formula_candidate_items(blocks):
        candidates.append(str(item["latex"]))
    return candidates


def _inline_formula_candidate_items(blocks: list[DocumentBlock]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for block in blocks:
        if block.block_type == BlockType.FORMULA:
            continue
        content = str(block.content or "")
        if not content:
            continue
        evidence_by_latex = _inline_math_evidence_by_latex(block)
        for index, latex in enumerate(_inline_formula_snippets_from_text(content)):
            evidence = evidence_by_latex.get(latex, {})
            bbox = evidence.get("bbox") if isinstance(evidence, dict) else None
            candidates.append(
                {
                    "candidate_id": f"{block.id}_inline_{index}",
                    "latex": latex,
                    "page_num": block.page_num,
                    "bbox": bbox if isinstance(bbox, list) and len(bbox) == 4 else list(block.bbox),
                    "block_id": block.id,
                    "source_context": _context_excerpt(content),
                    "inline_pdf_evidence": evidence,
                }
            )
    return candidates


def _inline_math_evidence_by_latex(block: DocumentBlock) -> dict[str, dict[str, object]]:
    raw_items = block.metadata.get("inline_math_candidates")
    if not isinstance(raw_items, list):
        return {}
    grouped: dict[str, dict[str, object]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        latex = str(item.get("latex", "") or "").strip()
        if not latex:
            continue
        grouped.setdefault(latex, item)
    return grouped


def _formula_accuracy_report(
    case: Any,
    store: FormulaIndexStore,
    doc_hash: str,
    blocks: list[DocumentBlock],
    formula_blocks: list[DocumentBlock],
    max_pages: int,
) -> dict[str, object]:
    """Compare every formula stage/model against bundled LaTeX sources.

    The metric is candidate-centric: each candidate LaTeX string is matched
    against the page-limited source formulas, so small r2/r3 samples can still
    show whether they improve over r0 on the formulas they touched.  This is
    not a substitute for full SyncTeX alignment, but it is the hard gate that
    prevents treating "tool returned something" as "formula is correct".
    """
    raw_latex_root = getattr(case, "latex_root", None)
    if not raw_latex_root:
        return {
            "available": False,
            "reason": "latex_root_missing",
            "stage_metrics": [],
            "monotonic": {"checked": False, "reason": "latex_root_missing"},
        }
    latex_root = Path(raw_latex_root)
    if not latex_root.exists():
        return {
            "available": False,
            "reason": "latex_root_missing",
            "stage_metrics": [],
            "monotonic": {"checked": False, "reason": "latex_root_missing"},
        }

    extraction = _extract_source_formulas_detailed(
        latex_root,
        pdf=Path(getattr(case, "pdf")),
        max_pages=max(0, int(max_pages)),
    )
    source_formulas = extraction.display + extraction.inline
    groups: dict[str, list[str]] = {}
    if formula_blocks:
        groups["parsed_blocks:document_chunker"] = [
            block.content
            for block in formula_blocks
            if str(block.content or "").strip()
        ]
    inline_candidates = _inline_formula_candidates_from_blocks(blocks)
    if inline_candidates:
        groups["inline_spans:document_chunker"] = inline_candidates
    for record in store.list_recognition_results(doc_hash, limit=10000):
        latex = str(record.latex or "").strip()
        if not latex:
            continue
        key = f"{record.stage}:{record.model}"
        groups.setdefault(key, []).append(latex)

    fusion_best: list[str] = []
    fusion_accepted: list[str] = []
    for record in store.list_fusion_records(doc_hash, limit=10000):
        payload = record.result_json if isinstance(record.result_json, dict) else {}
        best_latex = str(payload.get("best_latex", "") or "").strip()
        if best_latex:
            fusion_best.append(best_latex)
        if record.decision == "ready_for_manual_accept":
            accepted_latex = _accepted_latex_from_fusion_payload(payload) or best_latex
            if accepted_latex:
                fusion_accepted.append(accepted_latex)
    if fusion_best:
        groups[f"fusion_best:{FUSION_VERSION}"] = fusion_best
    if fusion_accepted:
        groups[f"fusion_accepted:{FUSION_VERSION}"] = fusion_accepted

    r3_suggestions: list[str] = []
    for record in store.list_round_records(
        doc_hash,
        statuses={"done"},
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        limit=10000,
    ):
        latex = str(record.result_json.get("suggested_latex", "") or "").strip()
        if latex:
            r3_suggestions.append(latex)
    if r3_suggestions:
        groups["cloud_semantic:suggested_latex"] = r3_suggestions

    stage_metrics = [
        _accuracy_for_group(name, latex_values, source_formulas, inline_sources=extraction.inline)
        for name, latex_values in sorted(groups.items())
    ]
    stage_metrics = [item for item in stage_metrics if item["candidate_count"] > 0]
    return {
        "available": True,
        "latex_root": str(latex_root),
        "source_formula_snippets": len(source_formulas),
        "source_display_snippets": len(extraction.display),
        "source_inline_snippets": len(extraction.inline),
        "source_coverage": extraction.coverage,
        "stage_metrics": stage_metrics,
        "monotonic": _monotonic_accuracy_summary(stage_metrics),
        "strict_quality_gate": _strict_quality_gate(stage_metrics),
        "note": (
            "Metrics are candidate-centric best-source LaTeX similarities. "
            "High product accuracy still requires page/bbox/source alignment "
            "and accepted-result gates before knowledge-base writeback."
        ),
    }


def _formula_fusion_report(
    case: Any,
    store: FormulaIndexStore,
    doc_hash: str,
    blocks: list[DocumentBlock],
    formula_blocks: list[DocumentBlock],
    max_pages: int,
    filepath: str = "",
) -> dict[str, object]:
    raw_latex_root = getattr(case, "latex_root", None)
    source_formulas: list[str] = []
    source_available = bool(raw_latex_root and Path(raw_latex_root).exists())
    if source_available:
        extraction = _extract_source_formulas_detailed(
            Path(raw_latex_root),
            pdf=Path(getattr(case, "pdf")),
            max_pages=max(0, int(max_pages)),
        )
        source_formulas = extraction.display + extraction.inline

    candidates_by_id: dict[str, list[dict[str, object]]] = {}
    for block in formula_blocks:
        latex = str(block.content or "").strip()
        if not latex:
            continue
        _add_fusion_candidate(
            candidates_by_id,
            _fusion_candidate(
                candidate_id=block.id,
                result_id=_synthetic_result_id(
                    doc_hash,
                    block.id,
                    "parsed_blocks",
                    "document_chunker",
                    _block_input_hash(block),
                ),
                stage="parsed_blocks",
                model="document_chunker",
                model_version="document_chunker",
                preprocess_version="parsed-block",
                input_hash=_block_input_hash(block),
                latex=latex,
                source_formulas=source_formulas,
                score=None,
                warnings=[],
                accepted=False,
                evidence={"page_num": block.page_num, "bbox": list(block.bbox)},
            )
        )

    for item in _inline_formula_candidate_items(blocks):
        candidate_id = str(item["candidate_id"])
        inline_latex = str(item["latex"])
        input_hash = _json_hash(item)
        _add_fusion_candidate(
            candidates_by_id,
            _fusion_candidate(
                candidate_id=candidate_id,
                result_id=_synthetic_result_id(
                    doc_hash,
                    candidate_id,
                    "inline_spans",
                    "document_chunker",
                    input_hash,
                ),
                stage="inline_spans",
                model="document_chunker",
                model_version="document_chunker",
                preprocess_version="math-font-inline",
                input_hash=input_hash,
                latex=inline_latex,
                source_formulas=source_formulas,
                score=None,
                warnings=[],
                accepted=False,
                evidence={
                    "source": "paragraph_inline_math",
                    "page_num": item.get("page_num"),
                    "bbox": item.get("bbox"),
                    "block_id": item.get("block_id"),
                    "source_context": item.get("source_context", ""),
                    "inline_pdf_evidence": item.get("inline_pdf_evidence", {}),
                },
            )
        )

    for record in store.list_recognition_results(doc_hash, limit=10000):
        if not str(record.latex or "").strip():
            continue
        _add_fusion_candidate(
            candidates_by_id,
            _fusion_candidate(
                candidate_id=record.candidate_id,
                result_id=record.result_id,
                stage=record.stage,
                model=record.model,
                model_version=record.model_version,
                preprocess_version=record.preprocess_version,
                input_hash=record.input_hash,
                latex=record.latex,
                source_formulas=source_formulas,
                score=record.score,
                warnings=list(record.warnings),
                accepted=record.accepted,
                evidence=record.evidence,
            )
        )
        decoded = record.evidence.get("decoded_latex", {}) if isinstance(record.evidence, dict) else {}
        decoded_latex = str(decoded.get("latex", "") or "").strip() if isinstance(decoded, dict) else ""
        if decoded_latex and decoded_latex != str(record.latex or "").strip():
            _add_fusion_candidate(
                candidates_by_id,
                _fusion_candidate(
                    candidate_id=record.candidate_id,
                    result_id=_synthetic_result_id(
                        doc_hash,
                        record.candidate_id,
                        "tinybdmath_decoded",
                        "decoded_latex",
                        record.input_hash,
                    ),
                    stage="tinybdmath_decoded",
                    model=record.model,
                    model_version=record.model_version,
                    preprocess_version=f"{record.preprocess_version}+decoded",
                    input_hash=record.input_hash,
                    latex=decoded_latex,
                    source_formulas=source_formulas,
                    score=_optional_float(decoded.get("confidence")) if isinstance(decoded, dict) else None,
                    warnings=[str(item) for item in decoded.get("warnings", []) if str(item)] if isinstance(decoded, dict) else [],
                    accepted=False,
                    evidence={
                        "source": "tinybdmath_decoded_latex",
                        "source_result_id": record.result_id,
                        "decoder_version": decoded.get("decoder_version", "") if isinstance(decoded, dict) else "",
                        "candidate_only": True,
                    },
                )
            )

    for record in store.list_round_records(
        doc_hash,
        statuses={"done"},
        scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        limit=10000,
    ):
        latex = str(record.result_json.get("suggested_latex", "") or "").strip()
        if not latex:
            continue
        _add_fusion_candidate(
            candidates_by_id,
            _fusion_candidate(
                candidate_id=record.target_id,
                result_id=_synthetic_result_id(
                    doc_hash,
                    record.target_id,
                    "cloud_semantic",
                    "suggested_latex",
                    _round_record_input_hash(record.result_json),
                ),
                stage="cloud_semantic",
                model="suggested_latex",
                model_version=str(record.result_json.get("model", "semantic_review") or "semantic_review"),
                preprocess_version="r3-review-json",
                input_hash=_round_record_input_hash(record.result_json),
                latex=latex,
                source_formulas=source_formulas,
                score=_optional_float(record.result_json.get("confidence")),
                warnings=[str(item) for item in record.result_json.get("risks", []) if str(item)]
                if isinstance(record.result_json.get("risks"), list)
                else [],
                accepted=False,
                evidence={"reason": record.result_json.get("reason", "")},
            )
        )

    rows = [
        _fusion_row(candidate_id, candidates)
        for candidate_id, candidates in sorted(candidates_by_id.items())
    ]
    rows = [row for row in rows if row["candidate_count"] > 0]
    persisted = _persist_fusion_rows(store, doc_hash, filepath, rows, formula_blocks)
    accepted_ready = [row for row in rows if row["decision"] == "ready_for_manual_accept"]
    needs_more = [row for row in rows if row["decision"] == "needs_more_evidence"]
    insufficient_evidence = [
        row for row in rows
        if row["decision"] != "ready_for_manual_accept"
        and float(row["best_similarity"]) < 0.90
    ]
    targeted_r2_rows = [row for row in insufficient_evidence if _row_needs_targeted_r2(row)]
    return {
        "available": bool(rows),
        "source_available": source_available,
        "candidate_rows": rows,
        "summary": {
            "candidate_count": len(rows),
            "ready_for_manual_accept": len(accepted_ready),
            "needs_more_evidence": len(needs_more),
            "missing_or_insufficient_r2": len(targeted_r2_rows),
            "local_precise_degraded": sum(
                1
                for row in rows
                if "local_precise_degraded_against_born_digital" in row.get("risk_flags", [])
            ),
            "inline_candidate_only_needs_review": len(insufficient_evidence) - len(targeted_r2_rows),
            "persisted": persisted,
            "average_best_similarity": round(
                sum(float(row["best_similarity"]) for row in rows) / len(rows),
                3,
            ) if rows else 0.0,
        },
        "targeted_r2_queue": [
            {
                "candidate_id": row["candidate_id"],
                "reason": "low_similarity_without_local_precise_candidate",
                "best_similarity": row["best_similarity"],
                "best_stage": row["best_stage"],
            }
            for row in targeted_r2_rows[:20]
        ],
        "note": (
            "Fusion ranks existing candidates only. It does not rewrite LaTeX. "
            "Rows below gate remain candidate-only and must not update正文/RAG/GraphRAG."
        ),
    }


def _add_fusion_candidate(
    groups: dict[str, list[dict[str, object]]],
    candidate: dict[str, object],
) -> None:
    existing_group_id = str(candidate.get("candidate_id", ""))
    group_id = _matching_fusion_group(groups, candidate)
    if existing_group_id in groups and existing_group_id != group_id:
        groups.setdefault(group_id, []).extend(groups.pop(existing_group_id))
    groups.setdefault(group_id, []).append(candidate)


def _matching_fusion_group(
    groups: dict[str, list[dict[str, object]]],
    candidate: dict[str, object],
) -> str:
    candidate_id = str(candidate.get("candidate_id", ""))
    if str(candidate.get("stage", "")) == "inline_spans":
        return candidate_id
    page_num = _candidate_page(candidate)
    bbox = _candidate_bbox(candidate)
    if page_num is not None and bbox is not None:
        for group_id, existing_items in groups.items():
            for existing in existing_items:
                if str(existing.get("stage", "")) == "inline_spans":
                    continue
                existing_bbox = _candidate_bbox(existing)
                if existing_bbox is None or _candidate_page(existing) != page_num:
                    continue
                if _bbox_iou(bbox, existing_bbox) >= 0.80:
                    return group_id
    if candidate_id in groups:
        return candidate_id
    for group_id, existing_items in groups.items():
        if any(str(existing.get("candidate_id", "")) == candidate_id for existing in existing_items):
            return group_id
    return candidate_id


def _fusion_candidate(
    *,
    candidate_id: str,
    result_id: str,
    stage: str,
    model: str,
    model_version: str,
    preprocess_version: str,
    input_hash: str,
    latex: str,
    source_formulas: list[str],
    score: float | None,
    warnings: list[str],
    accepted: bool,
    evidence: dict[str, object],
) -> dict[str, object]:
    similarity, best_source = _best_source_similarity(latex, source_formulas)
    return {
        "candidate_id": candidate_id,
        "result_id": result_id,
        "stage": stage,
        "model": model,
        "model_version": model_version,
        "preprocess_version": preprocess_version,
        "input_hash": input_hash,
        "latex": " ".join(str(latex or "").split())[:260],
        "source_similarity": similarity,
        "best_source": best_source,
        "score": score,
        "warnings": warnings,
        "accepted": accepted,
        "evidence": evidence,
    }


def _fusion_row(candidate_id: str, candidates: list[dict[str, object]]) -> dict[str, object]:
    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item.get("source_similarity", 0.0) or 0.0),
            0 if item.get("stage") == "parsed_blocks" else 1,
            float(item.get("score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else {}
    stages = sorted({str(item.get("stage", "")) for item in candidates if str(item.get("stage", ""))})
    local_precise = [item for item in candidates if str(item.get("stage", "")) == "local_precise"]
    member_candidate_ids = sorted(
        {str(item.get("candidate_id", "")) for item in candidates if str(item.get("candidate_id", ""))}
    )
    model_outputs = {
        f"{item.get('stage')}:{item.get('model')}": str(item.get("latex", ""))
        for item in candidates
    }
    result_ids = [
        str(item.get("result_id", ""))
        for item in ranked
        if str(item.get("result_id", ""))
    ]
    agreement = _candidate_agreement([str(item.get("latex", "")) for item in candidates])
    best_similarity = float(best.get("source_similarity", 0.0) or 0.0)
    stage_quality = _fusion_stage_quality(candidates)
    warnings = [
        warning
        for item in candidates
        for warning in item.get("warnings", [])
        if str(warning)
    ]
    risk_flags = _fusion_risk_flags(warnings)
    if _local_precise_degraded(stage_quality):
        risk_flags.append("local_precise_degraded_against_born_digital")
    if _best_result_prefers_degraded_local_precise(best, stage_quality):
        risk_flags.append("best_result_is_degraded_local_precise")
    risk_flags = sorted(set(risk_flags))
    gate = _fusion_gate(
        best_similarity=best_similarity,
        agreement=agreement,
        warnings=warnings,
        has_local_precise=bool(local_precise),
        stage_quality=stage_quality,
        risk_flags=risk_flags,
    )
    if gate["passed"]:
        decision = "ready_for_manual_accept"
    elif best_similarity < 0.90 or not local_precise or "local_precise_degraded_against_born_digital" in risk_flags:
        decision = "needs_more_evidence"
    else:
        decision = "candidate_only"
    return {
        "candidate_id": candidate_id,
        "member_candidate_ids": member_candidate_ids,
        "fusion_version": FUSION_VERSION,
        "fusion_input_hash": _fusion_input_hash(candidates),
        "best_result_id": str(best.get("result_id", "")),
        "ranked_result_ids": result_ids,
        "coverage": _fusion_coverage(candidates),
        "candidate_count": len(candidates),
        "stages": stages,
        "models": sorted(model_outputs),
        "best_stage": str(best.get("stage", "")),
        "best_model": str(best.get("model", "")),
        "best_latex": str(best.get("latex", "")),
        "best_similarity": round(best_similarity, 3),
        "best_source": str(best.get("best_source", "")),
        "agreement_score": agreement,
        "stage_quality": stage_quality,
        "has_local_precise": bool(local_precise),
        "syntax_valid": _looks_like_latex_candidate(str(best.get("latex", ""))),
        "risk_flags": risk_flags,
        "accepted_gate": gate,
        "decision": decision,
        "ranked_candidates": ranked[:6],
    }


def _fusion_gate(
    *,
    best_similarity: float,
    agreement: float,
    warnings: list[str],
    has_local_precise: bool,
    stage_quality: dict[str, dict[str, object]],
    risk_flags: list[str],
) -> dict[str, object]:
    reasons: list[str] = []
    if best_similarity < 0.90:
        reasons.append(f"best_similarity {best_similarity:.3f} < 0.900")
    if agreement < 0.75:
        reasons.append(f"agreement_score {agreement:.3f} < 0.750")
    if not has_local_precise:
        reasons.append("missing_local_precise_candidate")
    high_risk_warnings = [
        warning for warning in warnings
        if any(marker in str(warning) for marker in ("failed", "empty", "low_confidence", "prose_like", "table"))
    ]
    if high_risk_warnings:
        reasons.append("high_risk_warnings_present")
    if "local_precise_degraded_against_born_digital" in risk_flags:
        reasons.append("local_precise_degraded_against_born_digital")
    elif (local_quality := stage_quality.get("local_precise")) and (
        structure_quality := _best_born_digital_stage_quality(stage_quality)
    ):
        local_score = float(local_quality.get("best_similarity", 0.0) or 0.0)
        structure_score = float(structure_quality.get("best_similarity", 0.0) or 0.0)
        if local_score + 0.02 < structure_score:
            reasons.append(
                f"local_precise_best {local_score:.3f} < born_digital_best {structure_score:.3f}"
            )
    return {
        "passed": not reasons,
        "reasons": reasons,
        "thresholds": {
            "best_similarity": 0.90,
            "agreement_score": 0.75,
            "requires_local_precise": True,
            "local_precise_may_not_degrade_born_digital_by_more_than": 0.02,
        },
    }


def _accepted_latex_from_fusion_payload(payload: dict[str, object]) -> str:
    ranked = payload.get("ranked_candidates", [])
    if not isinstance(ranked, list):
        return ""
    for item in ranked:
        if not isinstance(item, dict) or not bool(item.get("accepted")):
            continue
        latex = str(item.get("latex", "") or "").strip()
        if latex:
            return latex
    return ""


def _fusion_stage_quality(candidates: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    quality: dict[str, dict[str, object]] = {}
    for item in candidates:
        stage = str(item.get("stage", "") or "")
        if not stage:
            continue
        similarity = float(item.get("source_similarity", 0.0) or 0.0)
        existing = quality.get(stage)
        if existing is None or similarity > float(existing.get("best_similarity", 0.0) or 0.0):
            quality[stage] = {
                "best_similarity": round(similarity, 3),
                "best_result_id": str(item.get("result_id", "") or ""),
                "best_model": str(item.get("model", "") or ""),
                "candidate_count": sum(1 for candidate in candidates if str(candidate.get("stage", "") or "") == stage),
            }
    return quality


def _best_born_digital_stage_quality(stage_quality: dict[str, dict[str, object]]) -> dict[str, object] | None:
    born_digital_stages = ("pdf_structure", "parsed_blocks", "inline_spans", "tinybdmath_structural")
    candidates = [
        stage_quality[stage]
        for stage in born_digital_stages
        if stage in stage_quality
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("best_similarity", 0.0) or 0.0))


def _local_precise_degraded(stage_quality: dict[str, dict[str, object]]) -> bool:
    local_quality = stage_quality.get("local_precise")
    structure_quality = _best_born_digital_stage_quality(stage_quality)
    if not local_quality or not structure_quality:
        return False
    local_score = float(local_quality.get("best_similarity", 0.0) or 0.0)
    structure_score = float(structure_quality.get("best_similarity", 0.0) or 0.0)
    if structure_score <= 0.0:
        return False
    return local_score + 0.02 < structure_score


def _best_result_prefers_degraded_local_precise(
    best: dict[str, object],
    stage_quality: dict[str, dict[str, object]],
) -> bool:
    return str(best.get("stage", "") or "") == "local_precise" and _local_precise_degraded(stage_quality)


def _persist_fusion_rows(
    store: FormulaIndexStore,
    doc_hash: str,
    filepath: str,
    rows: list[dict[str, object]],
    formula_blocks: list[DocumentBlock],
) -> dict[str, int]:
    if not doc_hash or not rows:
        return {
            "fusion_records_upserted": 0,
            "r2_queued": 0,
            "r3_queued": 0,
            "r5_queued": 0,
            "already_done_same_input": 0,
        }
    block_map = {block.id: block for block in formula_blocks}
    fusion_upserted = 0
    same_input = 0
    r2_targets: list[DocumentBlock] = []
    r3_targets: list[DocumentBlock] = []
    r3_payloads: dict[str, dict[str, object]] = {}
    r5_targets: list[DocumentBlock] = []
    r5_payloads: dict[str, dict[str, object]] = {}
    for row in rows:
        candidate_id = str(row.get("candidate_id", "") or "")
        input_hash = str(row.get("fusion_input_hash", "") or "")
        if not candidate_id or not input_hash:
            continue
        existing = store.get_fusion_record(
            doc_hash=doc_hash,
            candidate_id=candidate_id,
            fusion_version=FUSION_VERSION,
            input_hash=input_hash,
        )
        if existing is not None:
            same_input += 1
            continue
        store.put_fusion_record(
            doc_hash=doc_hash,
            candidate_id=candidate_id,
            fusion_version=FUSION_VERSION,
            input_hash=input_hash,
            best_result_id=str(row.get("best_result_id", "") or ""),
            ranked_result_ids=[
                str(item)
                for item in row.get("ranked_result_ids", [])
                if str(item)
            ] if isinstance(row.get("ranked_result_ids"), list) else [],
            coverage=float(row.get("coverage", 0.0) or 0.0),
            agreement_score=float(row.get("agreement_score", 0.0) or 0.0),
            source_similarity=float(row.get("best_similarity", 0.0) or 0.0),
            syntax_valid=bool(row.get("syntax_valid")),
            risk_flags=[
                str(item)
                for item in row.get("risk_flags", [])
                if str(item)
            ] if isinstance(row.get("risk_flags"), list) else [],
            accepted_gate=row.get("accepted_gate") if isinstance(row.get("accepted_gate"), dict) else {},
            decision=str(row.get("decision", "candidate_only") or "candidate_only"),
            result_json=row,
        )
        fusion_upserted += 1
        if _row_needs_targeted_r2(row):
            target = _fusion_target_block(row, block_map)
            if target is not None:
                r2_targets.append(target)
        if str(row.get("decision", "")) in {"needs_more_evidence", "ready_for_manual_accept"}:
            target = _fusion_review_block(row, block_map)
            if target is not None:
                r3_targets.append(target)
                r3_payloads[target.id] = {
                    "stage": FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
                    "input_hash": input_hash,
                    "fusion_version": FUSION_VERSION,
                    "best_result_id": str(row.get("best_result_id", "") or ""),
                    "decision": str(row.get("decision", "") or ""),
                    "candidate_count": int(row.get("candidate_count", 0) or 0),
                    "review_priority": target.metadata.get("semantic_review_priority", 0.0),
                    "review_priority_reason": target.metadata.get("semantic_review_priority_reason", ""),
                    "model": "pending_semantic_review",
                    "model_version": "pending_semantic_review",
                    "review_candidate": {
                        "latex": target.content,
                        "page_num": target.page_num,
                        "bbox": list(target.bbox),
                        "source": target.metadata.get("source", "formula_fusion_review"),
                        "source_block_id": target.metadata.get("source_block_id", ""),
                        "source_context": target.metadata.get("source_context", ""),
                        "inline_pdf_evidence": target.metadata.get("inline_pdf_evidence", {}),
                    },
                }
        if _row_has_accepted_result(row):
            target = _accepted_knowledge_block(row, block_map)
            if target is not None:
                r5_targets.append(target)
                r5_payloads[target.id] = {
                    "input_hash": input_hash,
                    "fusion_version": FUSION_VERSION,
                    "best_result_id": str(row.get("best_result_id", "") or ""),
                    "decision": str(row.get("decision", "") or ""),
                    "accepted_latex": target.content,
                }
    r2_queued = store.enqueue_blocks(
        doc_hash,
        filepath,
        _dedupe_blocks(r2_targets),
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
    ) if filepath and r2_targets else 0
    r3_queued = store.enqueue_round_records(
        doc_hash,
        filepath,
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        _dedupe_blocks(r3_targets),
        result_json_by_target=r3_payloads,
    ) if filepath and r3_targets else 0
    r5_queued = store.enqueue_round_records(
        doc_hash,
        filepath,
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        _dedupe_blocks(r5_targets),
        result_json_by_target=r5_payloads,
    ) if filepath and r5_targets else 0
    return {
        "fusion_records_upserted": fusion_upserted,
        "r2_queued": r2_queued,
        "r3_queued": r3_queued,
        "r5_queued": r5_queued,
        "already_done_same_input": same_input,
    }


def _row_needs_targeted_r2(row: dict[str, object]) -> bool:
    if str(row.get("decision", "")) != "needs_more_evidence":
        return False
    if bool(row.get("has_local_precise")):
        return False
    stages = {
        str(stage)
        for stage in row.get("stages", [])
        if str(stage)
    } if isinstance(row.get("stages"), list) else set()
    if stages and stages <= {"inline_spans"}:
        return False
    return True


def _row_has_accepted_result(row: dict[str, object]) -> bool:
    return any(
        isinstance(item, dict) and bool(item.get("accepted"))
        for item in row.get("ranked_candidates", [])
        if isinstance(item, dict)
    )


def _fusion_target_block(
    row: dict[str, object],
    block_map: dict[str, DocumentBlock],
) -> DocumentBlock | None:
    candidate_id = str(row.get("candidate_id", "") or "")
    block = block_map.get(candidate_id)
    if block is not None:
        return block.model_copy(
            update={
                "metadata": {
                    **block.metadata,
                    "needs_ocr": True,
                    "source": block.metadata.get("source", "formula_fusion_targeted_r2"),
                    "review_trigger": "formula_fusion_needs_more_evidence",
                    "fusion_input_hash": str(row.get("fusion_input_hash", "") or ""),
                    "inline_pdf_evidence": block.metadata.get("inline_pdf_evidence", {}),
                }
            },
            deep=True,
        )
    candidate = _best_candidate_with_bbox(row)
    if candidate is None:
        return None
    bbox = _candidate_bbox(candidate)
    page_num = _candidate_page(candidate)
    if bbox is None or page_num is None:
        return None
    return DocumentBlock(
        id=candidate_id,
        page_num=page_num,
        block_type=BlockType.FORMULA,
        content=str(candidate.get("latex", "") or ""),
        bbox=bbox,
        metadata={
            "needs_ocr": True,
            "source": "formula_fusion_targeted_r2",
            "review_trigger": "formula_fusion_needs_more_evidence",
            "formula_score": float(row.get("best_similarity", 0.0) or 0.0),
            "fusion_input_hash": str(row.get("fusion_input_hash", "") or ""),
            "source_block_id": str(candidate.get("evidence", {}).get("block_id", "") or "")
            if isinstance(candidate.get("evidence"), dict) else "",
            "source_context": str(candidate.get("evidence", {}).get("source_context", "") or "")
            if isinstance(candidate.get("evidence"), dict) else "",
            "inline_pdf_evidence": candidate.get("evidence", {}).get("inline_pdf_evidence", {})
            if isinstance(candidate.get("evidence"), dict) else {},
        },
    )


def _accepted_knowledge_block(
    row: dict[str, object],
    block_map: dict[str, DocumentBlock],
) -> DocumentBlock | None:
    candidate_id = str(row.get("candidate_id", "") or "")
    block = block_map.get(candidate_id)
    if block is None:
        return None
    accepted = next(
        (
            item for item in row.get("ranked_candidates", [])
            if isinstance(item, dict) and bool(item.get("accepted")) and str(item.get("latex", "")).strip()
        ),
        None,
    )
    if accepted is None:
        return None
    return block.model_copy(
        update={
            "content": str(accepted.get("latex", "") or ""),
            "metadata": {
                **block.metadata,
                "formula_fusion_accepted": True,
                "formula_fusion_input_hash": str(row.get("fusion_input_hash", "") or ""),
                "formula_fusion_best_result_id": str(row.get("best_result_id", "") or ""),
            },
        },
        deep=True,
    )


def _fusion_review_block(
    row: dict[str, object],
    block_map: dict[str, DocumentBlock],
) -> DocumentBlock | None:
    candidate_id = str(row.get("candidate_id", "") or "")
    review_priority = _semantic_review_priority(row)
    block = block_map.get(candidate_id)
    if block is not None:
        return block.model_copy(
            update={
                "metadata": {
                    **block.metadata,
                    "fusion_input_hash": str(row.get("fusion_input_hash", "") or ""),
                    "fusion_decision": str(row.get("decision", "") or ""),
                    "semantic_review_priority": review_priority,
                    "semantic_review_priority_reason": _semantic_review_priority_reason(row),
                }
            },
            deep=True,
        )
    target = _fusion_target_block(row, block_map)
    if target is None:
        return None
    target.metadata["fusion_decision"] = str(row.get("decision", "") or "")
    target.metadata["semantic_review_priority"] = review_priority
    target.metadata["semantic_review_priority_reason"] = _semantic_review_priority_reason(row)
    return target


def _semantic_review_priority(row: dict[str, object]) -> float:
    """Rank r3 cloud review work by expected value without accepting candidates."""
    stages = {
        str(stage)
        for stage in row.get("stages", [])
        if str(stage)
    } if isinstance(row.get("stages"), list) else set()
    risk_flags = [
        str(flag)
        for flag in row.get("risk_flags", [])
        if str(flag)
    ] if isinstance(row.get("risk_flags"), list) else []
    best_latex = str(row.get("best_latex", "") or "").strip()
    candidate_count = int(row.get("candidate_count", 0) or 0)
    best_similarity = float(row.get("best_similarity", 0.0) or 0.0)
    coverage = float(row.get("coverage", 0.0) or 0.0)
    decision = str(row.get("decision", "") or "")

    score = 500.0
    if "inline_spans" in stages and len(stages) == 1:
        score -= 120.0
    if stages.intersection({"parsed_blocks", "pdf_structure"}):
        score += 180.0
    if "local_precise" in stages:
        score += 90.0
    if candidate_count > 1:
        score += min(float(candidate_count - 1) * 30.0, 120.0)
    score += max(0.0, 1.0 - best_similarity) * 220.0
    score += coverage * 80.0
    if decision == "ready_for_manual_accept":
        score += 80.0
    if risk_flags:
        score += min(float(len(risk_flags)) * 45.0, 180.0)
    if "local_precise_degraded_against_born_digital" in risk_flags:
        score += 140.0

    complexity = _latex_review_complexity(best_latex)
    score += complexity * 16.0
    if _is_low_value_inline_review(row, best_latex):
        score -= 180.0
    return round(max(score, 1.0), 3)


def _semantic_review_priority_reason(row: dict[str, object]) -> str:
    stages = ",".join(str(stage) for stage in row.get("stages", []) if str(stage)) \
        if isinstance(row.get("stages"), list) else ""
    risk_count = len(row.get("risk_flags", [])) if isinstance(row.get("risk_flags"), list) else 0
    latex = str(row.get("best_latex", "") or "").strip()
    return (
        f"stages={stages or 'none'}; "
        f"candidate_count={int(row.get('candidate_count', 0) or 0)}; "
        f"best_similarity={float(row.get('best_similarity', 0.0) or 0.0):.3f}; "
        f"coverage={float(row.get('coverage', 0.0) or 0.0):.3f}; "
        f"risk_count={risk_count}; "
        f"complexity={_latex_review_complexity(latex):.3f}; "
        f"low_value_inline={_is_low_value_inline_review(row, latex)}"
    )


def _latex_review_complexity(latex: str) -> float:
    text = str(latex or "").strip()
    if not text:
        return 0.0
    normalized = (
        text.replace(r"\(", "")
        .replace(r"\)", "")
        .replace("$$", "")
        .replace("$", "")
        .strip()
    )
    if not normalized:
        return 0.0
    score = min(len(normalized) / 18.0, 2.0)
    score += min(normalized.count("\\") * 0.35, 1.4)
    score += min(sum(1 for char in normalized if char.isdigit()) * 0.18, 0.9)
    score += min(sum(1 for char in normalized if char in "_^{}[]()") * 0.14, 1.2)
    score += min(sum(1 for char in normalized if char in "+-=<>/|,") * 0.12, 1.0)
    if any(ord(char) > 127 for char in normalized):
        score += 0.5
    return round(score, 3)


def _is_low_value_inline_review(row: dict[str, object], latex: str) -> bool:
    stages = {
        str(stage)
        for stage in row.get("stages", [])
        if str(stage)
    } if isinstance(row.get("stages"), list) else set()
    if stages != {"inline_spans"}:
        return False
    if int(row.get("candidate_count", 0) or 0) != 1:
        return False
    if row.get("risk_flags"):
        return False
    normalized = _normalize_formula_for_match(latex)
    if not normalized:
        return True
    if len(normalized) <= 1:
        return True
    if len(normalized) <= 2 and normalized.isalpha():
        return True
    return False


def _fusion_graph_blocks(formula_fusion: dict[str, object]) -> list[DocumentBlock]:
    rows = formula_fusion.get("candidate_rows", [])
    if not isinstance(rows, list):
        return []
    blocks: list[DocumentBlock] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        block = _fusion_candidate_block(row)
        if block is not None:
            blocks.append(block)
    return _dedupe_blocks(blocks)


def _fusion_candidate_block(row: dict[str, object]) -> DocumentBlock | None:
    candidate_id = str(row.get("candidate_id", "") or "")
    best_latex = str(row.get("best_latex", "") or "").strip()
    if not candidate_id or not best_latex:
        return None
    candidate = _best_candidate_with_bbox(row)
    if candidate is None:
        return None
    bbox = _candidate_bbox(candidate)
    page_num = _candidate_page(candidate)
    if bbox is None or page_num is None:
        return None
    gate = row.get("accepted_gate", {})
    gate_passed = bool(gate.get("passed")) if isinstance(gate, dict) else False
    decision = str(row.get("decision", "") or "")
    return DocumentBlock(
        id=candidate_id,
        page_num=page_num,
        block_type=BlockType.FORMULA,
        content=best_latex,
        bbox=bbox,
        metadata={
            "source": "formula_fusion_graph_candidate",
            "candidate_only": not gate_passed,
            "fusion_decision": decision,
            "fusion_input_hash": str(row.get("fusion_input_hash", "") or ""),
            "fusion_best_result_id": str(row.get("best_result_id", "") or ""),
            "formula_score": float(row.get("best_similarity", 0.0) or 0.0),
        },
    )


def _best_candidate_with_bbox(row: dict[str, object]) -> dict[str, object] | None:
    ranked = row.get("ranked_candidates", [])
    if not isinstance(ranked, list):
        return None
    for item in ranked:
        if isinstance(item, dict) and _candidate_bbox(item) is not None and _candidate_page(item) is not None:
            return item
    return None


def _dedupe_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    deduped: dict[str, DocumentBlock] = {}
    for block in blocks:
        deduped.setdefault(block.id, block)
    return list(deduped.values())


def _fusion_input_hash(candidates: list[dict[str, object]]) -> str:
    payload = [
        {
            "candidate_id": str(item.get("candidate_id", "")),
            "result_id": str(item.get("result_id", "")),
            "stage": str(item.get("stage", "")),
            "model": str(item.get("model", "")),
            "model_version": str(item.get("model_version", "")),
            "preprocess_version": str(item.get("preprocess_version", "")),
            "input_hash": str(item.get("input_hash", "")),
            "latex": str(item.get("latex", "")),
            "warnings": [str(value) for value in item.get("warnings", []) if str(value)]
            if isinstance(item.get("warnings"), list) else [],
        }
        for item in sorted(
            candidates,
            key=lambda value: (
                str(value.get("candidate_id", "")),
                str(value.get("stage", "")),
                str(value.get("model", "")),
                str(value.get("input_hash", "")),
            ),
        )
    ]
    return _json_hash(payload)


def _block_input_hash(block: DocumentBlock) -> str:
    return _json_hash(
        {
            "id": block.id,
            "page_num": block.page_num,
            "bbox": [round(float(value), 3) for value in block.bbox],
            "content": block.content,
        }
    )


def _round_record_input_hash(payload: dict[str, object]) -> str:
    return _json_hash(payload)


def _context_excerpt(text: str, limit: int = 500) -> str:
    compact = " ".join(str(text or "").split())
    return compact[: max(0, int(limit))]


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _synthetic_result_id(
    doc_hash: str,
    candidate_id: str,
    stage: str,
    model: str,
    input_hash: str,
) -> str:
    return _json_hash(
        {
            "doc_hash": doc_hash,
            "candidate_id": candidate_id,
            "stage": stage,
            "model": model,
            "input_hash": input_hash,
        }
    )


def _json_hash(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8", errors="ignore")).hexdigest()


def _json_ready(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _fusion_coverage(candidates: list[dict[str, object]]) -> float:
    stages = {str(item.get("stage", "")) for item in candidates if str(item.get("stage", ""))}
    expected = {"parsed_blocks", "pdf_structure", "local_precise", "cloud_semantic"}
    if not expected:
        return 0.0
    return round(len(stages.intersection(expected)) / len(expected), 3)


def _fusion_risk_flags(warnings: list[str]) -> list[str]:
    flags: list[str] = []
    for warning in warnings:
        text = str(warning)
        lower = text.lower()
        if any(marker in lower for marker in ("failed", "empty", "low_confidence", "prose_like", "table")):
            flags.append(text)
    return sorted(set(flags))


def _looks_like_latex_candidate(latex: str) -> bool:
    text = str(latex or "").strip()
    if not text:
        return False
    return text.count("{") == text.count("}") and text.count("[") == text.count("]")


def _candidate_agreement(latex_values: list[str]) -> float:
    values = _unique_nonempty(latex_values)
    if len(values) <= 1:
        return 1.0 if values else 0.0
    normalized = [_normalize_formula_for_match(value) for value in values]
    scores: list[float] = []
    for index, left in enumerate(normalized):
        for right in normalized[index + 1:]:
            if not left or not right:
                continue
            scores.append(_formula_similarity(left, right))
    return round(sum(scores) / len(scores), 3) if scores else 0.0


def _best_source_similarity(latex: str, source_formulas: list[str]) -> tuple[float, str]:
    normalized = _normalize_formula_for_match(latex)
    if not normalized or not source_formulas:
        return 0.0, ""
    best_score = 0.0
    best_source = ""
    for source in source_formulas:
        source_norm = _normalize_formula_for_match(source)
        if not source_norm:
            continue
        score = _formula_similarity(normalized, source_norm)
        if score > best_score:
            best_score = score
            best_source = source
    return round(best_score, 3), " ".join(best_source.split())[:260]


def _candidate_page(candidate: dict[str, object]) -> int | None:
    evidence = candidate.get("evidence")
    if not isinstance(evidence, dict):
        return None
    page_num = evidence.get("page_num")
    if isinstance(page_num, int):
        return page_num
    try:
        return int(page_num) if page_num is not None else None
    except (TypeError, ValueError):
        return None


def _candidate_bbox(candidate: dict[str, object]) -> tuple[float, float, float, float] | None:
    evidence = candidate.get("evidence")
    if not isinstance(evidence, dict):
        return None
    bbox = evidence.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        left, top, right, bottom = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_area = _bbox_area(left)
    right_area = _bbox_area(right)
    if left_area <= 0 or right_area <= 0:
        return 0.0
    ix0 = max(left[0], right[0])
    iy0 = max(left[1], right[1])
    ix1 = min(left[2], right[2])
    iy1 = min(left[3], right[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    return intersection / (left_area + right_area - intersection)


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _accuracy_for_group(
    group: str,
    candidates: list[str],
    source_formulas: list[str],
    *,
    inline_sources: list[str] | None = None,
) -> dict[str, object]:
    unique_candidates = _unique_nonempty(candidates)
    inline_sources = inline_sources or []
    if not unique_candidates or not source_formulas:
        return {
            "group": group,
            "candidate_count": len(unique_candidates),
            "exact_match_rate": 0.0,
            "near_match_rate": 0.0,
            "weak_match_rate": 0.0,
            "inline_near_match_rate": 0.0,
            "inline_weak_match_rate": 0.0,
            "inline_unmatched_count": len(inline_sources),
            "average_best_similarity": 0.0,
            "low_similarity_candidate_count": len(unique_candidates),
            "sample_low_similarity": [],
        }
    matches, _low_source, metrics = _best_formula_matches(
        unique_candidates,
        source_formulas,
        max_sources=len(unique_candidates),
        max_candidates_per_source=80,
    )
    _, _, inline_metrics = _best_formula_matches(
        inline_sources,
        unique_candidates,
        max_sources=len(inline_sources),
        max_candidates_per_source=80,
    )
    low_similarity = [
        {
            "candidate_index": int(item.get("source_index", -1)),
            "candidate": str(item.get("source", "")),
            "best_source": str(item.get("pdf", "")),
            "similarity": float(item.get("similarity", 0.0) or 0.0),
        }
        for item in matches
        if float(item.get("similarity", 0.0) or 0.0) < 0.55
    ]
    return {
        "group": group,
        "candidate_count": len(unique_candidates),
        "exact_match_count": int(metrics["exact"]),
        "near_match_count": int(metrics["near"]),
        "weak_match_count": int(metrics["weak"]),
        "exact_match_rate": round(int(metrics["exact"]) / len(unique_candidates), 3),
        "near_match_rate": round(float(metrics["near_rate"]), 3),
        "weak_match_rate": round(float(metrics["weak_rate"]), 3),
        "inline_near_match_rate": round(float(inline_metrics["near_rate"]), 3),
        "inline_weak_match_rate": round(float(inline_metrics["weak_rate"]), 3),
        "inline_unmatched_count": int(inline_metrics["unmatched"]),
        "average_best_similarity": round(float(metrics["average"]), 3),
        "low_similarity_candidate_count": len(low_similarity),
        "sample_low_similarity": low_similarity[:5],
    }


def _monotonic_accuracy_summary(stage_metrics: list[dict[str, object]]) -> dict[str, object]:
    def metric_for(group: str) -> dict[str, object] | None:
        items = [
            item
            for item in stage_metrics
            if str(item.get("group", "")).startswith(group)
        ]
        if not items:
            return None
        best = max(items, key=lambda item: float(item.get("average_best_similarity", 0.0) or 0.0))
        return {
            "average_best_similarity": float(best.get("average_best_similarity", 0.0) or 0.0),
            "candidate_count": sum(int(item.get("candidate_count", 0) or 0) for item in items),
            "best_group": str(best.get("group", "")),
        }

    parsed = metric_for("parsed_blocks:")
    r0 = metric_for("pdf_structure:")
    r2 = metric_for("local_precise:")
    r3 = metric_for("cloud_semantic:")
    checks: list[dict[str, object]] = []
    for label_from, label_to, left, right in (
        ("parsed_blocks", "r0_pdf_structure", parsed, r0),
        ("r0_pdf_structure", "r2_local_high_precision", r0, r2),
        ("r2_local_high_precision", "r3_cloud_semantic_review", r2, r3),
    ):
        if left is None or right is None:
            continue
        left_score = float(left["average_best_similarity"])
        right_score = float(right["average_best_similarity"])
        left_count = int(left["candidate_count"])
        right_count = int(right["candidate_count"])
        checks.append({
            "from": label_from,
            "to": label_to,
            "accuracy_passed": right_score >= left_score,
            "coverage_comparable": right_count >= left_count,
            "delta": round(right_score - left_score, 3),
            "from_candidate_count": left_count,
            "to_candidate_count": right_count,
            "from_best_group": left["best_group"],
            "to_best_group": right["best_group"],
        })
    accuracy_non_decreasing = all(bool(item["accuracy_passed"]) for item in checks) if checks else False
    coverage_comparable = all(bool(item["coverage_comparable"]) for item in checks) if checks else False
    return {
        "checked": bool(checks),
        "accuracy_non_decreasing": accuracy_non_decreasing,
        "coverage_comparable": coverage_comparable,
        "passed": accuracy_non_decreasing and coverage_comparable,
        "checks": checks,
        "best_average_similarity": {
            "parsed_blocks": parsed,
            "r0_pdf_structure": r0,
            "r2_local_high_precision": r2,
            "r3_cloud_semantic_review": r3,
        },
    }


def _strict_quality_gate(stage_metrics: list[dict[str, object]]) -> dict[str, object]:
    thresholds = {
        "near_match_rate": 0.95,
        "average_best_similarity": 0.90,
        "low_similarity_candidate_count": 0,
    }
    failures: list[dict[str, object]] = []
    for item in stage_metrics:
        group = str(item.get("group", ""))
        if not group.startswith(("pdf_structure:", "local_precise:", "cloud_semantic:")):
            continue
        near_match_rate = float(item.get("near_match_rate", 0.0) or 0.0)
        average = float(item.get("average_best_similarity", 0.0) or 0.0)
        low_count = int(item.get("low_similarity_candidate_count", 0) or 0)
        reasons: list[str] = []
        if near_match_rate < thresholds["near_match_rate"]:
            reasons.append(f"near_match_rate {near_match_rate:.3f} < {thresholds['near_match_rate']:.3f}")
        if average < thresholds["average_best_similarity"]:
            reasons.append(f"average_best_similarity {average:.3f} < {thresholds['average_best_similarity']:.3f}")
        if low_count > thresholds["low_similarity_candidate_count"]:
            reasons.append(f"low_similarity_candidate_count {low_count} > 0")
        if reasons:
            failures.append({"group": group, "reasons": reasons})
    return {
        "passed": not failures and bool(stage_metrics),
        "thresholds": thresholds,
        "failures": failures,
        "note": "Failing this gate means candidates may be stored for review but must not be accepted into正文/RAG/GraphRAG.",
    }


def _unique_nonempty(values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        text = " ".join(str(value or "").split())
        if text:
            unique.setdefault(text, text)
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run r0-r5 formula parsing pipeline.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="attention")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--r1-limit", type=int, default=4)
    parser.add_argument("--r2-limit", type=int, default=2)
    parser.add_argument(
        "--r2-sample-formulas",
        type=int,
        default=0,
        help="Explicitly send this many existing formula blocks to r2 candidate-only review.",
    )
    parser.add_argument("--r3-limit", type=int, default=2)
    parser.add_argument("--r4-limit", type=int, default=16)
    parser.add_argument("--r5-limit", type=int, default=8)
    parser.add_argument("--auto-local-tools", action="store_true")
    parser.add_argument("--run-cloud-review", action="store_true")
    parser.add_argument(
        "--run-tinybdmath",
        action="store_true",
        help="Run non-visual TinyBDMath r2a structural candidate scoring after r0/r0.5.",
    )
    parser.add_argument(
        "--tinybdmath-model",
        type=Path,
        default=None,
        help="Optional JSON model from tools/tinybdmath_train_baseline.py.",
    )
    parser.add_argument(
        "--tinybdmath-edge-model",
        type=Path,
        default=None,
        help="Deprecated no-op; legacy edge scorer is baseline-only and is not used by r2a.",
    )
    parser.add_argument(
        "--tinybdmath-graph-parser-model",
        type=Path,
        default=None,
        help="Graph Parser JSON artifact from tools/tinybdmath_train_graph_parser.py.",
    )
    parser.add_argument("--drain-r2", action="store_true", help="Run r2 in bounded batches until no queued r2 jobs remain.")
    parser.add_argument("--drain-r3", action="store_true", help="Run r3 in bounded batches until no queued r3 jobs remain.")
    parser.add_argument("--drain-r4", action="store_true", help="Run r4 in bounded batches until no queued r4 jobs remain.")
    parser.add_argument("--drain-r5", action="store_true", help="Run r5 in bounded batches until no queued r5 jobs remain.")
    parser.add_argument(
        "--run-targeted-r2-after-fusion",
        action="store_true",
        help="After fusion queues low-evidence candidates, immediately run one bounded r2 batch.",
    )
    parser.add_argument(
        "--reuse-db",
        action="store_true",
        help="Keep existing job databases to verify second-open skip behavior.",
    )
    parser.add_argument("--formula-db", default="test_artifacts/formula_multiround/formula_jobs.db")
    parser.add_argument("--graph-db", default="test_artifacts/formula_multiround/graph_jobs.db")
    parser.add_argument("--output", default="test_artifacts/formula_multiround/report.json")
    args = parser.parse_args()

    formula_db = ROOT / args.formula_db
    graph_db = ROOT / args.graph_db
    for db_path in (formula_db, graph_db):
        if db_path.exists() and not args.reuse_db:
            db_path.unlink()
        db_path.parent.mkdir(parents=True, exist_ok=True)

    reports = [
        run_pipeline_case(
            case,
            formula_db_path=formula_db,
            graph_db_path=graph_db,
            max_pages=max(1, args.max_pages),
            start_page=max(0, args.start_page),
            r1_limit=max(0, args.r1_limit),
            r2_limit=max(0, args.r2_limit),
            r3_limit=max(0, args.r3_limit),
            r4_limit=max(1, args.r4_limit),
            r5_limit=max(0, args.r5_limit),
            r2_sample_formulas=max(0, args.r2_sample_formulas),
            auto_local_tools=bool(args.auto_local_tools),
            run_cloud_review=bool(args.run_cloud_review),
            run_tinybdmath=bool(args.run_tinybdmath),
            tinybdmath_model=args.tinybdmath_model,
            tinybdmath_graph_parser_model=args.tinybdmath_graph_parser_model,
            tinybdmath_edge_model=args.tinybdmath_edge_model,
            run_targeted_r2_after_fusion=bool(args.run_targeted_r2_after_fusion),
            drain_r2=bool(args.drain_r2),
            drain_r3=bool(args.drain_r3),
            drain_r4=bool(args.drain_r4),
            drain_r5=bool(args.drain_r5),
        )
        for case in _select_cases(args.case)
    ]
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "args": _json_ready(vars(args)),
        "reports": [asdict(report) for report in reports],
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
