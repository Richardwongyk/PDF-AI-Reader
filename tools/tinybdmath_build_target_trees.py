from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="Build TinyBDMath CSLT target trees from graph-row source labels.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--latex-key", default="label_latex")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--progress-every", type=int, default=5000)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    builder = TinyBDTargetTreeBuilder()
    warnings: Counter[str] = Counter()
    structure_counts: Counter[str] = Counter()
    rows = 0
    success_rows = 0
    with (args.output_dir / "tinybdmath_target_trees.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in _iter_jsonl_chunks(args.graph_rows, batch_size=args.batch_size, limit=args.limit):
            target_rows, chunk_warnings, chunk_structures = builder.build_row_chunk(chunk, latex_key=args.latex_key)
            for row in target_rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            rows += len(target_rows)
            success_rows += sum(1 for item in target_rows if item.get("target_tree"))
            warnings.update(chunk_warnings)
            structure_counts.update(chunk_structures)
            if args.progress_every > 0 and rows % max(1, int(args.progress_every)) < len(target_rows):
                print(
                    json.dumps(
                        {
                            "event": "target_tree_progress",
                            "rows": rows,
                            "success_rows": success_rows,
                            "failed_rows": rows - success_rows,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    manifest = {
        "schema_version": "tinybdmath_target_tree_manifest_v1",
        "builder_version": "tinybdmath_katex_to_cslt_v3",
        "rows": rows,
        "success_rows": success_rows,
        "failed_rows": rows - success_rows,
        "latex_key": args.latex_key,
        "batch_size": max(1, int(args.batch_size)),
        "streaming": True,
        "warnings": dict(sorted(warnings.items())),
        "structure_counts": dict(sorted(structure_counts.items())),
        "notes": [
            "Target CSLT rows are for training/audit only.",
            "Production born-digital parsing must not read source LaTeX.",
        ],
    }
    (args.output_dir / "tinybdmath_target_tree_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _iter_jsonl_chunks(path: Path, *, batch_size: int, limit: int = 0) -> list[list[dict[str, Any]]]:
    chunk_size = max(1, int(batch_size or 1))
    row_limit = int(limit or 0)
    chunk: list[dict[str, Any]] = []
    total = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                chunk.append(value)
                total += 1
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []
            if row_limit > 0 and total >= row_limit:
                break
    if chunk:
        yield chunk


if __name__ == "__main__":
    raise SystemExit(main())
