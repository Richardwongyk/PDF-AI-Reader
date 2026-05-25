"""Train the optional PyTorch TinyBDMath MLP backend from JSONL rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_torch_backend import (
    TinyBDTorchConfig,
    TorchBackendUnavailable,
    train_torch_quality_model,
)
from tools.tinybdmath_train_baseline import _filter_rows, parse_quality_filter


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--hidden-units", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--min-similarity", type=float, default=0.0)
    parser.add_argument("--include-quality", action="append", default=[])
    args = parser.parse_args()

    rows = _filter_rows(
        _read_jsonl(args.rows),
        min_similarity=args.min_similarity,
        include_quality=parse_quality_filter(args.include_quality),
    )
    config = TinyBDTorchConfig(
        epochs=args.epochs,
        hidden_units=args.hidden_units,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        device=args.device,
    )
    try:
        payload = train_torch_quality_model(rows, args.output_dir, config=config)
    except TorchBackendUnavailable as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "torch_backend_unavailable",
                    "detail": str(exc),
                    "hint": "Run this in an isolated ML/torch conda env; do not install torch into the main app env just for this script.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
