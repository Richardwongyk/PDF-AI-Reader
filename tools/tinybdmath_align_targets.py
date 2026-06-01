from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.latex_mathml_extractor import read_jsonl
from src.core.tinybdmath_alignment import TinyBDAlignmentBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="Align TinyBDMath graph rows to CSLT target trees.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--target-trees", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    graph_rows = read_jsonl(args.graph_rows, limit=args.limit)
    target_rows = read_jsonl(args.target_trees, limit=args.limit)
    alignment_rows, manifest = TinyBDAlignmentBuilder().align_rows(graph_rows, target_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "tinybdmath_alignment_rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in alignment_rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (args.output_dir / "tinybdmath_alignment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
