"""Train the dependency-light TinyBDMath MLP on real-data rows.

Input rows come from ``tools/tinybdmath_training_data.py``.  The model artifact
is intentionally JSON so it can be inspected, versioned outside git artifacts,
and replaced by a PyTorch/ONNX worker later without changing callers.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_baseline import (
    TinyBDBaselineConfig,
    evaluate_baseline,
    train_baseline,
    train_validation_split,
)


def train_from_rows_file(
    rows_path: Path,
    output_dir: Path,
    *,
    epochs: int = 120,
    hidden_units: int = 24,
    learning_rate: float = 0.015,
    validation_fraction: float = 0.20,
    seed: int = 20260525,
    min_similarity: float = 0.0,
    include_quality: set[str] | None = None,
) -> dict[str, Any]:
    all_rows = _read_jsonl(rows_path)
    rows = _filter_rows(all_rows, min_similarity=min_similarity, include_quality=include_quality)
    train_rows, validation_rows = train_validation_split(
        rows,
        validation_fraction=validation_fraction,
        seed=seed,
    )
    config = TinyBDBaselineConfig(
        epochs=epochs,
        hidden_units=hidden_units,
        learning_rate=learning_rate,
        validation_fraction=validation_fraction,
        seed=seed,
    )
    model, train_metrics = train_baseline(train_rows, config)
    validation_metrics = evaluate_baseline(model, validation_rows)
    all_metrics = evaluate_baseline(model, rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "tinybdmath_mlp_quality_model.json"
    model.save(model_path)
    payload = {
        "schema_version": "tinybdmath_baseline_training_v1",
        "rows_path": str(rows_path),
        "model_path": str(model_path),
        "config": asdict(config),
        "row_counts": {
            "all_input": len(all_rows),
            "used": len(rows),
            "train": len(train_rows),
            "validation": len(validation_rows),
        },
        "train": train_metrics,
        "validation": validation_metrics,
        "all": all_metrics,
        "notes": [
            "LaTeX source similarity is used only for labels/evaluation.",
            "Inference features are derived from PDF glyph graph evidence only.",
            "The accepted gate is intentionally conservative and should remain candidate-only until real precision is proven.",
        ],
    }
    (output_dir / "tinybdmath_mlp_quality_eval.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    min_similarity: float,
    include_quality: set[str] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if include_quality is not None and str(row.get("quality_label", "")) not in include_quality:
            continue
        try:
            similarity = float(row.get("source_similarity", 0.0) or 0.0)
        except (TypeError, ValueError):
            similarity = 0.0
        if similarity < min_similarity:
            continue
        result.append(row)
    return result


def parse_quality_filter(value: str | list[str] | tuple[str, ...] | None) -> set[str] | None:
    if value is None:
        return None
    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = [str(item) for item in value]
    labels: set[str] = set()
    for raw in raw_items:
        labels.update(item.strip() for item in raw.split(",") if item.strip())
    return labels or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--hidden-units", type=int, default=24)
    parser.add_argument("--learning-rate", type=float, default=0.015)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=20260525)
    parser.add_argument("--min-similarity", type=float, default=0.0)
    parser.add_argument(
        "--include-quality",
        action="append",
        default=[],
        help="Quality labels to keep. Can be comma-separated or repeated.",
    )
    args = parser.parse_args()

    payload = train_from_rows_file(
        args.rows,
        args.output_dir,
        epochs=args.epochs,
        hidden_units=args.hidden_units,
        learning_rate=args.learning_rate,
        validation_fraction=args.validation_fraction,
        seed=args.seed,
        min_similarity=args.min_similarity,
        include_quality=parse_quality_filter(args.include_quality),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
