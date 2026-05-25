"""Score TinyBDMath training/candidate rows with a saved model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_scorer import TinyBDCandidateQualityScorer


def score_candidates(
    *,
    rows_path: Path,
    model_path: Path,
    output_path: Path,
    limit: int = 0,
) -> dict[str, Any]:
    rows = _read_jsonl(rows_path)
    if limit > 0:
        rows = rows[:limit]
    scorer = TinyBDCandidateQualityScorer.from_model_path(model_path)
    scores = scorer.score_rows(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for score in scores:
            handle.write(json.dumps(score.to_json(), ensure_ascii=False, separators=(",", ":")) + "\n")
    label_counts: dict[str, int] = {}
    gate_positive = 0
    for score in scores:
        label_counts[score.predicted_label] = label_counts.get(score.predicted_label, 0) + 1
        if score.gate.get("accepted_candidate"):
            gate_positive += 1
    return {
        "schema_version": "tinybdmath_candidate_scores_v1",
        "rows_path": str(rows_path),
        "model_path": str(model_path),
        "output_path": str(output_path),
        "rows": len(scores),
        "predicted_label_counts": dict(sorted(label_counts.items())),
        "gate_positive": gate_positive,
        "notes": [
            "Scores are candidate evidence only.",
            "Positive gate results still require external verifier/fusion before accepted write-back.",
        ],
    }


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
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    payload = score_candidates(
        rows_path=args.rows,
        model_path=args.model,
        output_path=args.output,
        limit=args.limit,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
