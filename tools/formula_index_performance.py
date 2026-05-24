"""Lightweight performance gate for multi-round formula indexing.

The benchmark intentionally avoids OCR/model inference. It measures the hot
path needed when a PDF is imported:
- parse born-digital PDF blocks,
- persist round-0 page scan jobs,
- persist round-1 cached-recognition formula jobs.
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

from src.app.formula_index_scheduler import FormulaIndexScheduler, FormulaScanTrigger
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.core.models import BlockType
from src.core.pdf_engine import DocumentChunker
from src.infra.file_hash import compute_sha256
from tools.formula_latex_audit import _cases


@dataclass
class FormulaIndexPerfReport:
    case: str
    pdf: str
    pages_scanned: int
    blocks: int
    formula_blocks: int
    pending_formula_blocks: int
    parse_sec: float
    persist_sec: float
    total_sec: float
    parse_ms_per_page: float
    persist_ms_per_page: float
    page_jobs: dict[str, int]
    formula_jobs: dict[str, int]
    round_jobs: dict[str, int]


def _select_cases(case_name: str) -> list[Any]:
    cases = _cases()
    if case_name == "all":
        return cases
    return [case for case in cases if case.name == case_name]


def benchmark_case(
    case: Any,
    db_path: Path,
    max_pages: int,
    start_page: int = 0,
) -> FormulaIndexPerfReport:
    started_total = time.perf_counter()
    started_parse = time.perf_counter()
    chunker = DocumentChunker(
        enable_born_digital_math=True,
        enable_born_digital_semantics=True,
        enable_legacy_formula_heuristic=False,
    )
    blocks = []
    doc = fitz.open(case.pdf)
    try:
        first_page = max(0, min(start_page, doc.page_count))
        last_page = min(doc.page_count, first_page + max_pages) if max_pages > 0 else doc.page_count
        for page_num in range(first_page, last_page):
            blocks.extend(chunker.chunk_page(doc, page_num))
        pages_scanned = max(0, last_page - first_page)
    finally:
        doc.close()
    parse_sec = time.perf_counter() - started_parse

    doc_hash = compute_sha256(str(case.pdf))[:16]
    store = FormulaIndexStore(str(db_path))
    scheduler = FormulaIndexScheduler()
    plan = scheduler.plan_for_pages(
        blocks,
        pages=set(),
        trigger=FormulaScanTrigger.BACKGROUND,
        page_count=max(pages_scanned, 1),
    )
    started_persist = time.perf_counter()
    store.enqueue_pages(
        doc_hash,
        str(case.pdf),
        list(range(start_page, start_page + pages_scanned)),
        scan_round=FormulaScanRound.PDF_STRUCTURE,
    )
    store.enqueue_blocks(
        doc_hash,
        str(case.pdf),
        plan.blocks,
        plan.priority_pages,
        scan_round=plan.scan_round,
    )
    formula_blocks = [block for block in blocks if block.block_type == BlockType.FORMULA]
    store.enqueue_round_records(
        doc_hash,
        str(case.pdf),
        FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        "block",
        formula_blocks,
    )
    persist_sec = time.perf_counter() - started_persist
    total_sec = time.perf_counter() - started_total
    pending_formula_blocks = [
        block for block in formula_blocks
        if block.metadata.get("needs_ocr") and not block.metadata.get("mfr_recognized")
    ]
    page_divisor = max(pages_scanned, 1)
    return FormulaIndexPerfReport(
        case=case.name,
        pdf=str(case.pdf),
        pages_scanned=pages_scanned,
        blocks=len(blocks),
        formula_blocks=len(formula_blocks),
        pending_formula_blocks=len(pending_formula_blocks),
        parse_sec=round(parse_sec, 4),
        persist_sec=round(persist_sec, 4),
        total_sec=round(total_sec, 4),
        parse_ms_per_page=round(parse_sec * 1000 / page_divisor, 3),
        persist_ms_per_page=round(persist_sec * 1000 / page_divisor, 3),
        page_jobs=store.page_counts(doc_hash),
        formula_jobs=store.counts(doc_hash),
        round_jobs=store.round_counts(doc_hash),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark formula indexing import path.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--max-pages", type=int, default=16)
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument(
        "--db",
        default="test_artifacts/formula_index_performance/formula_jobs.db",
    )
    parser.add_argument(
        "--output",
        default="test_artifacts/formula_index_performance/report.json",
    )
    args = parser.parse_args()

    db_path = ROOT / args.db
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    reports = [
        benchmark_case(
            case,
            db_path=db_path,
            max_pages=max(1, args.max_pages),
            start_page=max(0, args.start_page),
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
