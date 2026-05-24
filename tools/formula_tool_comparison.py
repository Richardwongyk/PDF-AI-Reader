"""Compare external formula tools on the same PDF formula samples.

The tool is deliberately candidate-only: it crops formula regions, runs
process-isolated workers, persists r2 recognition candidates, and reports
quality/performance signals.  It never accepts results or rewrites document
blocks.
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

from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.core.external_formula_tools import (
    ExternalFormulaCandidate,
    ExternalFormulaToolRunner,
    ExternalFormulaToolSpec,
)
from src.core.formula_detector import Pix2TextMFDDetector
from src.core.models import BlockType, DocumentBlock
from src.core.pdf_engine import DocumentChunker
from src.infra.file_hash import compute_sha256
from tools.formula_latex_audit import (
    _cases,
    _extract_source_formulas_detailed,
    _formula_similarity,
    _normalize_formula_for_match,
)


@dataclass
class FormulaToolSample:
    candidate_id: str
    page_num: int
    bbox: tuple[float, float, float, float]
    source_text: str
    input_hash: str
    image_bytes: int


@dataclass
class FormulaToolCandidateReport:
    candidate_id: str
    model: str
    model_version: str
    preprocess_version: str
    latex: str
    score: float | None
    duration_ms: int
    warnings: list[str]
    source_similarity: float
    best_source: str
    result_id: str


@dataclass
class FormulaToolSummary:
    model: str
    attempted: int
    nonempty: int
    failed: int
    average_duration_ms: float
    average_source_similarity: float
    best_source_similarity: float


@dataclass
class FormulaToolComparisonReport:
    case: str
    pdf: str
    doc_hash: str
    status: str
    pages_scanned: int
    formula_blocks: int
    sampled_blocks: int
    tool_specs: list[str]
    crop_sec: float
    tool_sec: float
    persist_sec: float
    samples: list[FormulaToolSample]
    candidates: list[FormulaToolCandidateReport]
    summary: list[FormulaToolSummary]
    round_jobs: dict[str, int]


def _select_cases(case_name: str) -> list[Any]:
    cases = _cases()
    if case_name == "all":
        return cases
    return [case for case in cases if case.name == case_name]


def _load_specs(specs_arg: str) -> list[ExternalFormulaToolSpec] | None:
    raw = str(specs_arg or "").strip()
    if not raw:
        return None
    payload: object
    try:
        path = Path(raw)
        is_path = path.exists()
    except OSError:
        is_path = False
    if is_path:
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(raw)
    return ExternalFormulaToolRunner._parse_specs(payload)


def _source_formulas_for_case(case: Any, max_pages: int, match_scope: str) -> list[str]:
    extraction = _extract_source_formulas_detailed(
        case.latex_root,
        pdf=case.pdf,
        max_pages=max_pages,
    )
    if match_scope == "display":
        return extraction.display
    if match_scope == "inline":
        return extraction.inline
    return extraction.display + extraction.inline


def _collect_formula_blocks(
    pdf: Path,
    start_page: int,
    max_pages: int,
    sample_limit: int,
) -> tuple[int, list[DocumentBlock], int]:
    chunker = DocumentChunker(
        enable_born_digital_math=True,
        enable_born_digital_semantics=True,
        enable_legacy_formula_heuristic=False,
    )
    doc = fitz.open(pdf)
    blocks: list[DocumentBlock] = []
    formula_blocks = 0
    try:
        first_page = max(0, min(start_page, doc.page_count))
        last_page = min(doc.page_count, first_page + max_pages) if max_pages > 0 else doc.page_count
        for page_num in range(first_page, last_page):
            page_blocks = chunker.chunk_page(doc, page_num)
            page_formulas = [
                block for block in page_blocks
                if block.block_type == BlockType.FORMULA
            ]
            formula_blocks += len(page_formulas)
            for block in page_formulas:
                if len(blocks) < sample_limit:
                    blocks.append(block.model_copy(deep=True))
        return max(0, last_page - first_page), blocks, formula_blocks
    finally:
        doc.close()


def _crop_formula_samples(
    pdf: Path,
    blocks: list[DocumentBlock],
    dpi: int,
) -> tuple[list[FormulaToolSample], list[tuple[str, bytes]], float]:
    samples: list[FormulaToolSample] = []
    images: list[tuple[str, bytes]] = []
    started = time.perf_counter()
    doc = fitz.open(pdf)
    try:
        for block in blocks:
            try:
                image = Pix2TextMFDDetector._crop_bbox_image(
                    doc,
                    block.page_num,
                    block.bbox,
                    dpi=dpi,
                    pad=6.0,
                )
            except Exception:
                image = b""
            if not image:
                continue
            input_hash = hashlib.sha256(image).hexdigest()
            samples.append(
                FormulaToolSample(
                    candidate_id=block.id,
                    page_num=block.page_num,
                    bbox=block.bbox,
                    source_text=(block.content or "")[:240],
                    input_hash=input_hash,
                    image_bytes=len(image),
                )
            )
            images.append((block.id, image))
        return samples, images, time.perf_counter() - started
    finally:
        doc.close()


def _best_source_match(latex: str, source_formulas: list[str]) -> tuple[float, str]:
    normalized = _normalize_formula_for_match(latex)
    if not normalized or not source_formulas:
        return 0.0, ""
    best_score = 0.0
    best_source = ""
    for source in source_formulas:
        score = _formula_similarity(
            normalized,
            _normalize_formula_for_match(source),
        )
        if score > best_score:
            best_score = score
            best_source = source
    return round(best_score, 3), " ".join(best_source.split())[:240]


def compare_case(
    case: Any,
    *,
    db_path: Path,
    start_page: int = 0,
    max_pages: int = 8,
    sample_limit: int = 6,
    dpi: int = 300,
    match_scope: str = "display",
    specs: list[ExternalFormulaToolSpec] | None = None,
    runner: ExternalFormulaToolRunner | None = None,
    source_formulas: list[str] | None = None,
) -> FormulaToolComparisonReport:
    pages_scanned, blocks, formula_blocks = _collect_formula_blocks(
        case.pdf,
        start_page=max(0, start_page),
        max_pages=max(1, max_pages),
        sample_limit=max(1, sample_limit),
    )
    samples, images, crop_sec = _crop_formula_samples(
        case.pdf,
        blocks,
        dpi=max(96, dpi),
    )
    sample_ids = {sample.candidate_id for sample in samples}
    sampled_blocks = [block for block in blocks if block.id in sample_ids]
    active_specs = specs if specs is not None else ExternalFormulaToolRunner.default_specs()
    tool_names = [spec.name for spec in active_specs if spec.enabled]
    doc_hash = compute_sha256(str(case.pdf))[:16]
    store = FormulaIndexStore(str(db_path))
    if sampled_blocks:
        store.enqueue_blocks(
            doc_hash,
            str(case.pdf),
            sampled_blocks,
            scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
        )
        store.mark_round_running(
            doc_hash,
            FormulaScanRound.LOCAL_HIGH_PRECISION,
            "block",
            [block.id for block in sampled_blocks],
        )

    if source_formulas is None:
        source_formulas = _source_formulas_for_case(case, max_pages=max_pages, match_scope=match_scope)

    started_tools = time.perf_counter()
    candidates: list[ExternalFormulaCandidate] = []
    status = "ok"
    if not images:
        status = "no_samples"
    elif not tool_names:
        status = "no_tools_configured"
    else:
        candidates = (runner or ExternalFormulaToolRunner()).recognize_images(images, specs=active_specs)
    tool_sec = time.perf_counter() - started_tools

    sample_by_id = {sample.candidate_id: sample for sample in samples}
    reports: list[FormulaToolCandidateReport] = []
    grouped: dict[str, list[dict[str, object]]] = {}
    started_persist = time.perf_counter()
    for candidate in candidates:
        sample = sample_by_id.get(candidate.candidate_id)
        if sample is None:
            continue
        warnings = list(candidate.warnings)
        if "candidate_only" not in warnings:
            warnings.append("candidate_only")
        source_similarity, best_source = _best_source_match(candidate.latex, source_formulas)
        result_id = store.put_recognition_result(
            doc_hash=doc_hash,
            candidate_id=candidate.candidate_id,
            stage="local_precise",
            model=candidate.model,
            model_version=candidate.model_version,
            preprocess_version=candidate.preprocess_version,
            input_hash=sample.input_hash,
            latex=candidate.latex,
            normalized_latex=candidate.latex,
            score=candidate.score,
            duration_ms=candidate.duration_ms,
            warnings=warnings,
            evidence={
                "scan_round": FormulaScanRound.LOCAL_HIGH_PRECISION.value,
                "source": "formula_tool_comparison",
                "page_num": sample.page_num,
                "bbox": sample.bbox,
                "source_similarity": source_similarity,
                "best_source": best_source,
            },
            accepted=False,
        )
        report = FormulaToolCandidateReport(
            candidate_id=candidate.candidate_id,
            model=candidate.model,
            model_version=candidate.model_version,
            preprocess_version=candidate.preprocess_version,
            latex=candidate.latex,
            score=candidate.score,
            duration_ms=candidate.duration_ms,
            warnings=warnings,
            source_similarity=source_similarity,
            best_source=best_source,
            result_id=result_id,
        )
        reports.append(report)
        grouped.setdefault(candidate.candidate_id, []).append(asdict(report))

    for sample in samples:
        sample_reports = grouped.get(sample.candidate_id, [])
        nonempty_reports = [
            item for item in sample_reports
            if str(item.get("latex", "") or "").strip()
        ]
        if nonempty_reports:
            best = max(
                nonempty_reports,
                key=lambda item: float(item.get("source_similarity", 0.0) or 0.0),
            )
            store.mark_done(
                doc_hash,
                sample.candidate_id,
                str(best.get("latex", "")),
                sample.input_hash,
                str(best.get("model", "")),
                scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
            )
            store.mark_round_done(
                doc_hash,
                FormulaScanRound.LOCAL_HIGH_PRECISION,
                "block",
                sample.candidate_id,
                {
                    "stage": "local_precise",
                    "input_hash": sample.input_hash,
                    "tool_count": len(sample_reports),
                    "tools": sample_reports,
                },
            )
        elif sample_reports:
            store.mark_round_failed(
                doc_hash,
                FormulaScanRound.LOCAL_HIGH_PRECISION,
                "block",
                sample.candidate_id,
                "all_tools_empty_or_failed",
                status="failed",
            )
        else:
            reason = status if status != "ok" else "no_tool_result"
            store.mark_skipped(
                doc_hash,
                sample.candidate_id,
                reason,
                scan_round=FormulaScanRound.LOCAL_HIGH_PRECISION,
            )
    persist_sec = time.perf_counter() - started_persist

    return FormulaToolComparisonReport(
        case=case.name,
        pdf=str(case.pdf),
        doc_hash=doc_hash,
        status=status,
        pages_scanned=pages_scanned,
        formula_blocks=formula_blocks,
        sampled_blocks=len(samples),
        tool_specs=tool_names,
        crop_sec=round(crop_sec, 3),
        tool_sec=round(tool_sec, 3),
        persist_sec=round(persist_sec, 3),
        samples=samples,
        candidates=reports,
        summary=_summarize_candidates(reports),
        round_jobs=store.round_counts(doc_hash),
    )


def _summarize_candidates(
    reports: list[FormulaToolCandidateReport],
) -> list[FormulaToolSummary]:
    by_model: dict[str, list[FormulaToolCandidateReport]] = {}
    for report in reports:
        by_model.setdefault(report.model, []).append(report)
    summaries: list[FormulaToolSummary] = []
    for model, items in sorted(by_model.items()):
        attempted = len(items)
        nonempty_items = [item for item in items if item.latex.strip()]
        failed = sum(
            1
            for item in items
            if any(warning.startswith("tool_failed:") for warning in item.warnings)
        )
        summaries.append(
            FormulaToolSummary(
                model=model,
                attempted=attempted,
                nonempty=len(nonempty_items),
                failed=failed,
                average_duration_ms=round(
                    sum(item.duration_ms for item in items) / max(1, attempted),
                    3,
                ),
                average_source_similarity=round(
                    sum(item.source_similarity for item in nonempty_items) / max(1, len(nonempty_items)),
                    3,
                ),
                best_source_similarity=round(
                    max((item.source_similarity for item in items), default=0.0),
                    3,
                ),
            )
        )
    return summaries


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare external formula tools on shared PDF samples.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="attention")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--sample-limit", type=int, default=6)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--match-scope", choices=["display", "inline", "all"], default="display")
    parser.add_argument("--specs", default="", help="JSON list/path of external tool specs.")
    parser.add_argument(
        "--db",
        default="test_artifacts/formula_tool_comparison/formula_jobs.db",
    )
    parser.add_argument(
        "--output",
        default="test_artifacts/formula_tool_comparison/report.json",
    )
    args = parser.parse_args()

    db_path = ROOT / args.db
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    specs = _load_specs(args.specs)
    reports = [
        compare_case(
            case,
            db_path=db_path,
            start_page=args.start_page,
            max_pages=args.max_pages,
            sample_limit=args.sample_limit,
            dpi=args.dpi,
            match_scope=args.match_scope,
            specs=specs,
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
