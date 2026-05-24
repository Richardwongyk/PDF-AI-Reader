"""Run an auditable r0-r4 formula parsing pipeline on bundled PDFs.

This command is a production-oriented smoke/benchmark runner.  It executes the
same persisted stores used by the app and emits a report with per-round status,
timing, skips/failures, and candidate counts.
"""

from __future__ import annotations

import argparse
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
from src.app.formula_semantic_review import FormulaSemanticReviewService
from src.app.graph_index_flow import run_graph_index_batch
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
)


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
    graph_jobs: dict[str, int]
    recognition_results: dict[str, int]
    formula_accuracy: dict[str, object]


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


def _select_cases(case_name: str) -> list[Any]:
    cases = _cases()
    if case_name == "all":
        return cases
    return [case for case in cases if case.name == case_name]


def _parse_blocks(pdf: Path, max_pages: int, start_page: int) -> tuple[int, list[DocumentBlock]]:
    chunker = DocumentChunker(
        enable_born_digital_math=True,
        enable_born_digital_semantics=True,
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
    r2_sample_formulas: int = 0,
    auto_local_tools: bool = False,
    run_cloud_review: bool = False,
) -> MultiRoundPipelineReport:
    started_total = time.perf_counter()
    pages_scanned, blocks = _parse_blocks(case.pdf, max_pages=max_pages, start_page=start_page)
    doc_hash = compute_sha256(str(case.pdf))[:16]
    formula_store = FormulaIndexStore(str(formula_db_path))
    graph_store = GraphIndexStore(str(graph_db_path))
    rounds: list[RoundReport] = []
    filepath = str(case.pdf)
    formula_blocks = [block for block in blocks if block.block_type == BlockType.FORMULA]

    flow = FormulaIndexFlow(store=formula_store)

    rounds.append(_run_r0(flow, formula_store, filepath, doc_hash, blocks, start_page, pages_scanned))
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
        )
    )
    rounds.append(_run_r3(formula_store, filepath, doc_hash, formula_blocks, limit=r3_limit, run_cloud_review=run_cloud_review))
    rounds.append(_run_r4(graph_store, filepath, doc_hash, blocks, limit=r4_limit))

    recognition_results = _recognition_result_counts(formula_store, doc_hash)
    formula_accuracy = _formula_accuracy_report(
        case,
        formula_store,
        doc_hash,
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
        graph_jobs=graph_store.counts(doc_hash),
        recognition_results=recognition_results,
        formula_accuracy=formula_accuracy,
    )


def _run_r0(
    flow: FormulaIndexFlow,
    store: FormulaIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    start_page: int,
    pages_scanned: int,
) -> RoundReport:
    started = time.perf_counter()
    pages = list(range(start_page, start_page + pages_scanned))
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
    status = "done" if failed == 0 else "partial"
    return RoundReport(
        round=FormulaScanRound.PDF_STRUCTURE.value,
        status=status,
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=counts,
        details={
            "queued_pages": queued,
            "processed_pages": len(pending_tasks),
            "done_pages": done_pages,
            "failed_pages": failed,
            "skipped_completed_pages": max(0, len(pages) - len(pending_tasks)),
        },
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


def _run_r2(
    store: FormulaIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    formula_blocks: list[DocumentBlock],
    limit: int,
    sample_formulas: int,
    auto_local_tools: bool,
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
    tasks = store.list_tasks(
        doc_hash,
        statuses={"queued"},
        scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
        limit=max(0, limit),
    )
    block_map = {block.id: block for block in blocks}
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
        return RoundReport(
            round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
            status="skipped",
            elapsed_sec=round(time.perf_counter() - started, 3),
            counts=store.round_counts(doc_hash, FormulaScanRound.LOCAL_HIGH_PRECISION),
            details={"reason": "no_pending_r2_blocks", "explicit_samples_queued": sampled},
        )

    external_tool_specs: list[ExternalFormulaToolSpec] | None = None
    discovered_tools: list[str] = []
    if auto_local_tools:
        external_tool_specs = ExternalFormulaToolRunner.known_local_specs()
        discovered_tools = [spec.name for spec in external_tool_specs]
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
        FormulaIndexFlow(store=store)._on_worker_finished(emitted[0], filepath, max(1, len(selected)))
    counts = store.round_counts(doc_hash, FormulaScanRound.LOCAL_HIGH_PRECISION)
    return RoundReport(
        round=FormulaScanRound.LOCAL_HIGH_PRECISION.value,
        status="done" if emitted else "partial",
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=counts,
        details={
            "processed_blocks": len(selected),
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
) -> RoundReport:
    started = time.perf_counter()
    queued = store.enqueue_round_records(
        doc_hash,
        filepath,
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        formula_blocks,
    )
    client = _cloud_review_client() if run_cloud_review else _MockReviewClient()
    service = FormulaSemanticReviewService(store, client, batch_size=max(1, limit), timeout_sec=90)
    counts = service.run_batch(doc_hash, formula_blocks, limit=max(0, limit))
    processed_total = sum(int(value) for value in counts.values())
    return RoundReport(
        round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
        status="done" if processed_total > 0 else ("queued" if queued else "skipped"),
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=store.round_counts(doc_hash, FormulaScanRound.CLOUD_SEMANTIC_REVIEW),
        details={
            "queued_reviews": queued,
            "processed": counts,
            "client": client.model_name,
            "cloud": run_cloud_review,
        },
    )


def _run_r4(
    store: GraphIndexStore,
    filepath: str,
    doc_hash: str,
    blocks: list[DocumentBlock],
    limit: int,
) -> RoundReport:
    started = time.perf_counter()
    result = run_graph_index_batch(
        store,
        filepath,
        doc_hash,
        blocks,
        batch_budget=max(1, limit),
    )
    return RoundReport(
        round=FormulaScanRound.KNOWLEDGE_GRAPH.value,
        status="done" if int(result.get("processed", 0) or 0) > 0 else "skipped",
        elapsed_sec=round(time.perf_counter() - started, 3),
        counts=store.counts(doc_hash),
        details=result,
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


def _formula_accuracy_report(
    case: Any,
    store: FormulaIndexStore,
    doc_hash: str,
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
    for record in store.list_recognition_results(doc_hash, limit=10000):
        latex = str(record.latex or "").strip()
        if not latex:
            continue
        key = f"{record.stage}:{record.model}"
        groups.setdefault(key, []).append(latex)

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
        _accuracy_for_group(name, latex_values, source_formulas)
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


def _accuracy_for_group(
    group: str,
    candidates: list[str],
    source_formulas: list[str],
) -> dict[str, object]:
    unique_candidates = _unique_nonempty(candidates)
    if not unique_candidates or not source_formulas:
        return {
            "group": group,
            "candidate_count": len(unique_candidates),
            "exact_match_rate": 0.0,
            "near_match_rate": 0.0,
            "weak_match_rate": 0.0,
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
    parser = argparse.ArgumentParser(description="Run r0-r4 formula parsing pipeline.")
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
    parser.add_argument("--auto-local-tools", action="store_true")
    parser.add_argument("--run-cloud-review", action="store_true")
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
            r2_sample_formulas=max(0, args.r2_sample_formulas),
            auto_local_tools=bool(args.auto_local_tools),
            run_cloud_review=bool(args.run_cloud_review),
        )
        for case in _select_cases(args.case)
    ]
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "args": vars(args),
        "reports": [asdict(report) for report in reports],
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
