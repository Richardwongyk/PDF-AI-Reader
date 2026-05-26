"""Train the dependency-light TinyBDMath graph baseline from graph rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_graph_baseline import train_graph_baseline


def train_from_graph_rows(
    rows_path: Path,
    output_dir: Path,
    *,
    limit: int = 0,
    epochs: int = 24,
    learning_rate: float = 0.05,
) -> dict[str, Any]:
    rows = _read_jsonl(rows_path, limit=limit)
    model, report = train_graph_baseline(rows, epochs=epochs, learning_rate=learning_rate)
    output_dir.mkdir(parents=True, exist_ok=True)
    model.save(output_dir / "tinybdmath_graph_baseline_model.json")
    (output_dir / "tinybdmath_graph_baseline_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def _read_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=24)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    args = parser.parse_args()
    report = train_from_graph_rows(
        args.rows,
        args.output_dir,
        limit=args.limit,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
