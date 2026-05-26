"""Build weak TinyBDMath relation labels from graph rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_relation_labels import (
    build_relation_label_dataset,
    read_graph_rows,
    read_mathml_rows,
    write_relation_label_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mathml-batch-size", type=int, default=512)
    parser.add_argument("--mathml-rows", type=Path, default=None)
    args = parser.parse_args()

    rows = read_graph_rows(args.rows, limit=args.limit)
    mathml_by_row_id = read_mathml_rows(args.mathml_rows) if args.mathml_rows else None
    result = build_relation_label_dataset(
        rows,
        mathml_batch_size=args.mathml_batch_size,
        mathml_by_row_id=mathml_by_row_id,
    )
    write_relation_label_dataset(result, args.output_dir)
    print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
