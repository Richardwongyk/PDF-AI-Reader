from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.core.tinybdmath_structural_candidate import (
    build_structural_candidates,
    build_structural_candidates_stream,
    read_jsonl,
    write_structural_candidates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build candidate-only TinyBDMath structural evidence.")
    parser.add_argument("--scores", required=True, type=Path, help="tinybdmath_relation_scores.jsonl")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-confidence", type=float, default=0.70)
    parser.add_argument("--max-outgoing-per-source", type=int, default=4)
    parser.add_argument("--stream", action="store_true", help="Read scores and write candidates incrementally.")
    args = parser.parse_args()

    if args.stream:
        manifest = build_structural_candidates_stream(
            args.scores,
            args.output_dir,
            limit=args.limit,
            min_confidence=args.min_confidence,
            max_outgoing_per_source=args.max_outgoing_per_source,
        )
    else:
        scored_rows = read_jsonl(args.scores, limit=args.limit)
        candidates, manifest = build_structural_candidates(
            scored_rows,
            min_confidence=args.min_confidence,
            max_outgoing_per_source=args.max_outgoing_per_source,
        )
        write_structural_candidates(candidates, manifest, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
