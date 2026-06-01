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
from src.core.tinybdmath_structural_candidate import TinyBDStructuralCandidateStreamWriter
from src.core.tinybdmath_relation_scorer import (
    read_jsonl,
    score_jsonl_stream,
    score_jsonl_stream_torch,
    score_rows,
    score_rows_torch,
    write_scored_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--max-edges", type=int, default=256)
    parser.add_argument("--stream", action="store_true", help="Score and write rows incrementally.")
    parser.add_argument("--fast-torch", action="store_true", help="Use PyTorch batched tensor inference.")
    parser.add_argument("--torch-device", default="cpu", help="PyTorch device for --fast-torch, e.g. cpu or cuda.")
    parser.add_argument("--batch-rows", type=int, default=512, help="Rows per PyTorch scoring batch.")
    parser.add_argument(
        "--compact-output",
        action="store_true",
        help="For --fast-torch, omit per-class probabilities and keep only fields needed by structural decode.",
    )
    parser.add_argument(
        "--direct-structural-output-dir",
        type=Path,
        help="For --stream --fast-torch, write structural candidates from the same scored batches without rereading score JSONL.",
    )
    parser.add_argument("--structural-min-confidence", type=float, default=0.70)
    parser.add_argument("--structural-max-outgoing-per-source", type=int, default=4)
    parser.add_argument(
        "--no-score-jsonl",
        action="store_true",
        help="For --stream --fast-torch with direct structural output, skip writing relation score JSONL.",
    )
    args = parser.parse_args()

    if args.no_score_jsonl and not args.direct_structural_output_dir:
        raise SystemExit("--no-score-jsonl requires --direct-structural-output-dir")

    model = TinyBDEdgeBaselineModel.load(args.model)
    if args.stream:
        if args.fast_torch:
            manifest: dict[str, object] | None = None
            structural_writer = (
                TinyBDStructuralCandidateStreamWriter(
                    args.direct_structural_output_dir,
                    min_confidence=args.structural_min_confidence,
                    max_outgoing_per_source=args.structural_max_outgoing_per_source,
                    source="direct_fast_torch_scoring",
                )
                if args.direct_structural_output_dir
                else None
            )
            manifest = score_jsonl_stream_torch(
                args.rows,
                model,
                args.output_dir,
                limit=args.limit,
                min_confidence=args.min_confidence,
                max_edges=args.max_edges,
                batch_rows=args.batch_rows,
                device=args.torch_device,
                compact_output=args.compact_output,
                scored_batch_callback=structural_writer.write_scored_rows if structural_writer else None,
                write_scores=not args.no_score_jsonl,
            )
            if structural_writer is not None:
                manifest = dict(manifest)
                manifest["direct_structural_candidates"] = structural_writer.close()
        else:
            if args.direct_structural_output_dir or args.no_score_jsonl:
                raise SystemExit("--direct-structural-output-dir and --no-score-jsonl require --fast-torch")
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
    if args.fast_torch:
        scored, manifest = score_rows_torch(
            read_jsonl(args.rows, limit=args.limit),
            model,
            min_confidence=args.min_confidence,
            max_edges=args.max_edges,
            batch_rows=args.batch_rows,
            device=args.torch_device,
            compact_output=args.compact_output,
        )
    else:
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
