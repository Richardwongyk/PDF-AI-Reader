"""Score TinyBDMath graph rows with a saved edge relation model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_edge_baseline import TinyBDEdgeBaselineModel
from src.core.tinybdmath_relation_scorer import read_jsonl, score_jsonl_stream, score_rows, write_scored_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--max-edges", type=int, default=256)
    parser.add_argument("--stream", action="store_true", help="Score and write rows incrementally.")
    args = parser.parse_args()

    model = TinyBDEdgeBaselineModel.load(args.model)
    if args.stream:
        manifest = score_jsonl_stream(
            args.rows,
            model,
            args.output_dir,
            limit=args.limit,
            min_confidence=args.min_confidence,
            max_edges=args.max_edges,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    scored, manifest = score_rows(
        read_jsonl(args.rows, limit=args.limit),
        model,
        min_confidence=args.min_confidence,
        max_edges=args.max_edges,
    )
    write_scored_rows(scored, manifest, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
