"""Apply TinyBDMath review decisions to produce verified gold rows.

This does not mutate the source dataset.  It writes a separate reviewed-gold
JSONL that can be used by later training scripts after human or model review.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tinybdmath_gold_policy import source_maps, verified_gold_blockers


SCHEMA_VERSION = "tinybdmath_review_apply_v1"


def apply_review_decisions(
    *,
    dataset_dir: Path,
    decisions_path: Path,
    output_dir: Path,
    include_auto_verified: bool = True,
    min_confidence: float = 0.95,
) -> dict[str, Any]:
    sources = _read_jsonl(dataset_dir / "source_formulas.jsonl")
    candidates = _read_jsonl(dataset_dir / "pdf_candidates.jsonl")
    decisions = _read_jsonl(decisions_path)
    source_by_key, source_scope_counts = source_maps(sources)
    candidate_by_review_id = {
        _review_id(row): row
        for row in candidates
        if _review_id(row)
    }

    rows: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    if include_auto_verified:
        for candidate in candidates:
            blockers = verified_gold_blockers(candidate, source_by_key, source_scope_counts)
            if blockers:
                continue
            rows.append(_gold_row(candidate, gold_latex=str(candidate.get("best_source_latex", "")), source="auto_verified"))

    for decision in decisions:
        review_id = str(decision.get("review_id", ""))
        candidate = candidate_by_review_id.get(review_id)
        if candidate is None:
            excluded["missing_candidate"] += 1
            continue
        decision_value = str(decision.get("decision", "")).strip().lower()
        if decision_value not in {"accept", "revise"}:
            excluded[f"decision_{decision_value or 'blank'}"] += 1
            continue
        confidence = _float(decision.get("confidence"))
        if confidence < min_confidence:
            excluded["low_review_confidence"] += 1
            continue
        if decision_value == "accept":
            gold_latex = str(decision.get("source_latex") or candidate.get("best_source_latex", "")).strip()
        else:
            gold_latex = str(decision.get("reviewer_latex", "")).strip()
        if not gold_latex:
            excluded["empty_gold_latex"] += 1
            continue
        rows.append(
            _gold_row(
                candidate,
                gold_latex=gold_latex,
                source=f"review_{decision_value}",
                decision=decision,
                confidence=confidence,
            )
        )

    rows = _dedupe_rows(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    verified_path = output_dir / "tinybdmath_verified_gold_rows.jsonl"
    _write_jsonl(verified_path, rows)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "dataset_dir": str(dataset_dir),
        "decisions_path": str(decisions_path),
        "output_dir": str(output_dir),
        "include_auto_verified": include_auto_verified,
        "min_confidence": min_confidence,
        "source_candidates": len(candidates),
        "review_decisions": len(decisions),
        "verified_gold_rows": len(rows),
        "gold_sources": dict(sorted(Counter(str(row.get("gold_source", "")) for row in rows).items())),
        "excluded": dict(sorted(excluded.items())),
        "verified_path": str(verified_path),
        "note": "Only auto-verified rows and high-confidence accept/revise decisions are exported.",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _gold_row(
    candidate: dict[str, Any],
    *,
    gold_latex: str,
    source: str,
    decision: dict[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    case = str(candidate.get("case", ""))
    candidate_id = str(candidate.get("candidate_id", ""))
    review_id = f"{case}:{candidate_id}"
    return {
        "schema_version": "tinybdmath_verified_gold_row_v1",
        "review_id": review_id,
        "case": case,
        "candidate_id": candidate_id,
        "page_num_zero_based": _int(candidate.get("page_num")),
        "page_num_one_based": _int(candidate.get("page_num")) + 1,
        "bbox": candidate.get("bbox", []),
        "gold_latex": gold_latex,
        "gold_source": source,
        "review_confidence": round(confidence, 6),
        "reviewer": str((decision or {}).get("reviewer", "")),
        "review_notes": str((decision or {}).get("notes", "")),
        "source_id": str(candidate.get("best_source_id", "")),
        "source_latex": str(candidate.get("best_source_latex", "")),
        "raw_source_latex": str(candidate.get("best_source_raw_latex", "")),
        "source_macro_expansion_warnings": candidate.get("source_macro_expansion_warnings", []),
        "source_similarity": candidate.get("best_source_similarity", 0.0),
        "page_match": str(candidate.get("page_match", "")),
        "pdf_text": str(candidate.get("pdf_text", "")),
        "r0_latex": str(candidate.get("r0_latex", "")),
        "glyph_count": _int(candidate.get("glyph_count")),
        "edge_count": _int(candidate.get("edge_count")),
        "edge_hint_counts": candidate.get("edge_hint_counts", {}),
        "r0_input_hash": str(candidate.get("r0_input_hash", "")),
        "feature_graph_hash": str(candidate.get("feature_graph_hash", "")),
        "enriched_graph_hash": str(candidate.get("enriched_graph_hash", "")),
    }


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    priority = {"auto_verified": 0, "review_accept": 1, "review_revise": 2}
    for row in rows:
        key = str(row.get("review_id", ""))
        old = by_key.get(key)
        if old is None:
            by_key[key] = row
            continue
        if priority.get(str(row.get("gold_source", "")), 0) >= priority.get(str(old.get("gold_source", "")), 0):
            by_key[key] = row
    return [by_key[key] for key in sorted(by_key)]


def _review_id(row: dict[str, Any]) -> str:
    case = str(row.get("case", ""))
    candidate_id = str(row.get("candidate_id", ""))
    if not case or not candidate_id:
        return ""
    return f"{case}:{candidate_id}"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-auto-verified", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=0.95)
    args = parser.parse_args()
    apply_review_decisions(
        dataset_dir=args.dataset_dir,
        decisions_path=args.decisions,
        output_dir=args.output_dir,
        include_auto_verified=not args.no_auto_verified,
        min_confidence=max(0.0, min(1.0, float(args.min_confidence))),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
