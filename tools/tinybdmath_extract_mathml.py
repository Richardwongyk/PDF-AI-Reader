from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.core.latex_mathml_extractor import extract_many, read_jsonl, write_extractions


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract KaTeX MathML/parse-tree evidence for TinyBDMath audit rows.")
    parser.add_argument("--rows", required=True, type=Path, help="JSONL rows containing label_latex/canonical labels.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--latex-key", default="label_latex")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()

    rows = read_jsonl(args.rows, limit=args.limit)
    extractions, manifest = extract_many(rows, latex_key=args.latex_key, batch_size=args.batch_size)
    write_extractions(extractions, manifest, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
