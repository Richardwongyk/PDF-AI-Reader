from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.core.tinybdmath_structural_eval import evaluate_structural_candidates, read_jsonl, write_eval_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate TinyBDMath structural candidates against relation labels.")
    parser.add_argument("--candidates", required=True, type=Path, help="tinybdmath_structural_candidates.jsonl")
    parser.add_argument("--relation-labels", required=True, type=Path, help="tinybdmath_relation_label_rows.jsonl")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--hard-only", action="store_true", help="Exclude weak labels from the comparison.")
    args = parser.parse_args()

    candidates = read_jsonl(args.candidates, limit=args.limit)
    labels = read_jsonl(args.relation_labels, limit=args.limit)
    report = evaluate_structural_candidates(candidates, labels, include_weak=not args.hard_only)
    write_eval_report(report, args.output)
    summary = {
        "schema_version": report["schema_version"],
        "rows": report["rows"],
        "micro": report["micro"],
        "warning_counts": report["warning_counts"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
