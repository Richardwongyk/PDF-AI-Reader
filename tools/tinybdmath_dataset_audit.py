"""Audit TinyBDMath real-data artifacts for coverage and leakage risks."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def audit_dataset(dataset_dir: Path, training_dir: Path | None = None) -> dict[str, Any]:
    sources = _read_jsonl(dataset_dir / "source_formulas.jsonl")
    candidates = _read_jsonl(dataset_dir / "pdf_candidates.jsonl")
    features = _read_jsonl(dataset_dir / "feature_graphs.jsonl")
    rows = _read_jsonl(training_dir / "tinybdmath_rows.jsonl") if training_dir else []

    candidate_cases = Counter(str(item.get("case", "")) for item in candidates)
    source_cases = Counter(str(item.get("case", "")) for item in sources)
    weak = [item for item in candidates if _float(item.get("best_source_similarity")) < 0.55]
    near = [item for item in candidates if _float(item.get("best_source_similarity")) >= 0.80]
    unknown = [
        item
        for item in candidates
        if _int((item.get("enriched_summary") or {}).get("unknown_after") if isinstance(item.get("enriched_summary"), dict) else 0) > 0
    ]
    quality = Counter(str(row.get("quality_label", "")) for row in rows)
    leakage_warnings = _leakage_warnings(rows)
    return {
        "schema_version": "tinybdmath_dataset_audit_v1",
        "dataset_dir": str(dataset_dir),
        "training_dir": str(training_dir) if training_dir else "",
        "source_formulas": len(sources),
        "pdf_candidates": len(candidates),
        "feature_graphs": len(features),
        "training_rows": len(rows),
        "source_cases": dict(sorted(source_cases.items())),
        "candidate_cases": dict(sorted(candidate_cases.items())),
        "quality_counts": dict(sorted(quality.items())),
        "candidate_rates": {
            "near_match_rate": round(len(near) / len(candidates), 6) if candidates else 1.0,
            "weak_or_worse_rate": round(len(weak) / len(candidates), 6) if candidates else 0.0,
            "unknown_glyph_candidate_rate": round(len(unknown) / len(candidates), 6) if candidates else 0.0,
        },
        "edge_stats": _edge_stats(candidates),
        "low_match_samples": _candidate_samples(weak),
        "unknown_glyph_samples": _candidate_samples(unknown),
        "leakage_warnings": leakage_warnings,
        "status": _status(candidates, rows, leakage_warnings),
    }


def _edge_stats(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {"average_glyphs": 0.0, "average_edges": 0.0, "hint_totals": {}}
    hint_totals: Counter[str] = Counter()
    for candidate in candidates:
        hints = candidate.get("edge_hint_counts", {})
        if isinstance(hints, dict):
            hint_totals.update({str(key): _int(value) for key, value in hints.items()})
    return {
        "average_glyphs": round(sum(_int(item.get("glyph_count")) for item in candidates) / len(candidates), 3),
        "average_edges": round(sum(_int(item.get("edge_count")) for item in candidates) / len(candidates), 3),
        "hint_totals": dict(sorted(hint_totals.items())),
    }


def _candidate_samples(candidates: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "case": item.get("case", ""),
            "candidate_id": item.get("candidate_id", ""),
            "page_num": item.get("page_num", 0),
            "similarity": item.get("best_source_similarity", 0.0),
            "glyph_count": item.get("glyph_count", 0),
            "edge_count": item.get("edge_count", 0),
            "pdf_text": str(item.get("pdf_text", ""))[:140],
            "best_source_latex": str(item.get("best_source_latex", ""))[:140],
        }
        for item in candidates[:limit]
    ]


def _leakage_warnings(rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    forbidden_feature_keys = {"source_similarity", "best_source_id", "latex_target"}
    for row in rows[:100]:
        feature_like = set(row.get("features", {}).keys()) if isinstance(row.get("features"), dict) else set()
        leaked = sorted(feature_like & forbidden_feature_keys)
        if leaked:
            warnings.append(f"feature_payload_contains_source_fields:{','.join(leaked)}")
            break
    return warnings


def _status(candidates: list[dict[str, Any]], rows: list[dict[str, Any]], warnings: list[str]) -> str:
    if warnings:
        return "blocked_leakage_risk"
    if not candidates or not rows:
        return "incomplete_missing_rows"
    if len(rows) != len(candidates):
        return "warning_row_candidate_mismatch"
    return "ok_candidate_only"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
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


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--training-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    payload = audit_dataset(args.dataset_dir, args.training_dir)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if payload["status"].startswith("ok") or payload["status"].startswith("warning") else 1


if __name__ == "__main__":
    raise SystemExit(main())
