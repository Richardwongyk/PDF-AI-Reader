"""Run the real Attention/Napkin TinyBDMath data-prep pipeline.

Outputs are written outside git-tracked source by default.  The LaTeX sources
are used only for dataset/evaluation labels.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.born_digital_formula_dataset import build_case_dataset, _select_cases, _write_jsonl_dataset
from tools.tinybdmath_dataset_audit import audit_dataset
from tools.tinybdmath_score_candidates import score_candidates
from tools.tinybdmath_train_baseline import train_from_rows_file
from tools.tinybdmath_training_data import build_training_rows, _write_jsonl


def run_pipeline(
    *,
    case_name: str,
    output_dir: Path,
    start_page: int = 0,
    max_pages: int = 0,
    match_scope: str = "all",
    train_model: bool = True,
    epochs: int = 120,
    hidden_units: int = 24,
) -> dict[str, Any]:
    started = time.perf_counter()
    dataset_dir = output_dir / "dataset"
    training_dir = output_dir / "training"
    reports = [
        build_case_dataset(
            case,
            start_page=start_page,
            max_pages=max_pages,
            match_scope=match_scope,
        )
        for case in _select_cases(case_name)
    ]
    _write_jsonl_dataset(dataset_dir, reports)
    rows, training_report = build_training_rows(dataset_dir)
    rows_path = training_dir / "tinybdmath_rows.jsonl"
    _write_jsonl(rows_path, [asdict(row) for row in rows])
    training_dir.mkdir(parents=True, exist_ok=True)
    (training_dir / "tinybdmath_report.json").write_text(
        json.dumps(asdict(training_report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    model_report = None
    score_report = None
    if train_model:
        model_report = train_from_rows_file(
            rows_path,
            output_dir / "model",
            epochs=epochs,
            hidden_units=hidden_units,
        )
        score_report = score_candidates(
            rows_path=rows_path,
            model_path=output_dir / "model" / "tinybdmath_mlp_quality_model.json",
            output_path=output_dir / "scores" / "tinybdmath_candidate_scores.jsonl",
        )
    audit_report = audit_dataset(dataset_dir, training_dir)
    (output_dir / "dataset_audit.json").write_text(
        json.dumps(audit_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "schema_version": "tinybdmath_realdata_pipeline_v1",
        "case": case_name,
        "output_dir": str(output_dir),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "dataset": [
            {
                "case": report.case,
                "pages_scanned": report.pages_scanned,
                "source_formulas": report.source_formulas,
                "candidates": report.candidates,
                "average_best_similarity": report.average_best_similarity,
                "weak_match_rate": report.weak_match_rate,
                "near_match_rate": report.near_match_rate,
                "feature_edges": report.feature_edges,
                "raw_unknown_glyphs": report.raw_unknown_glyphs,
                "repaired_glyphs": report.repaired_glyphs,
            }
            for report in reports
        ],
        "training": asdict(training_report),
        "audit": audit_report,
        "model": model_report,
        "scores": score_report,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=Path("test_artifacts/tinybdmath_realdata"))
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0, help="0 means full PDF")
    parser.add_argument("--match-scope", choices=["all", "display", "inline"], default="all")
    parser.add_argument("--skip-train", action="store_true", help="Only build dataset/training rows")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--hidden-units", type=int, default=24)
    args = parser.parse_args()

    summary = run_pipeline(
        case_name=args.case,
        output_dir=args.output_dir,
        start_page=args.start_page,
        max_pages=args.max_pages,
        match_scope=args.match_scope,
        train_model=not args.skip_train,
        epochs=args.epochs,
        hidden_units=args.hidden_units,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
