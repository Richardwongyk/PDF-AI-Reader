"""Audit source-gold and PDF-label quality for TinyBDMath datasets."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def audit_gold(dataset_dir: Path) -> dict[str, Any]:
    sources = _read_jsonl(dataset_dir / "source_formulas.jsonl")
    candidates = _read_jsonl(dataset_dir / "pdf_candidates.jsonl")
    source_empty = [row for row in sources if not str(row.get("latex", "")).strip()]
    source_duplicates = _duplicates(sources, key="normalized")
    env_counts = Counter(str(row.get("env", "")) for row in sources)
    parser_counts = Counter(str(row.get("parser_version", "")) for row in sources)
    candidate_label_counts = Counter(_label_tier(row) for row in candidates)
    page_match_counts = Counter(str(row.get("page_match", "")) or "missing" for row in candidates)
    tier_by_page_match: dict[str, dict[str, int]] = {}
    for row in candidates:
        page_match = str(row.get("page_match", "")) or "missing"
        tier_by_page_match.setdefault(page_match, {})
        tier = _label_tier(row)
        tier_by_page_match[page_match][tier] = tier_by_page_match[page_match].get(tier, 0) + 1
    risky = [
        row for row in candidates
        if _label_tier(row) in {"weak_label", "unmatched_label"}
    ]
    return {
        "schema_version": "tinybdmath_gold_audit_v1",
        "dataset_dir": str(dataset_dir),
        "source": {
            "rows": len(sources),
            "empty_formula_rows": len(source_empty),
            "duplicate_normalized_count": len(source_duplicates),
            "env_counts": dict(sorted(env_counts.items())),
            "parser_counts": dict(sorted(parser_counts.items())),
            "empty_samples": _samples(source_empty),
            "duplicate_samples": source_duplicates[:30],
            "gold_status": "ok" if not source_empty else "blocked_empty_source_formula",
            "note": "Source formulas are the gold inventory. They are extracted from LaTeX source delimiters/environments with offsets.",
        },
        "pdf_labels": {
            "candidate_rows": len(candidates),
            "label_tiers": dict(sorted(candidate_label_counts.items())),
            "page_match_counts": dict(sorted(page_match_counts.items())),
            "label_tiers_by_page_match": {
                key: dict(sorted(value.items()))
                for key, value in sorted(tier_by_page_match.items())
            },
            "risky_label_samples": _candidate_samples(risky),
            "note": "Only exact/verified labels should be used as gold targets. Near/weak/unmatched rows are useful for recall/error mining, not unquestioned supervised labels.",
        },
        "status": _status(source_empty, candidates),
    }


def _label_tier(row: dict[str, Any]) -> str:
    sim = _float(row.get("best_source_similarity"))
    if sim >= 0.999:
        return "exact_label"
    if sim >= 0.92:
        return "near_label"
    if sim >= 0.55:
        return "weak_label"
    return "unmatched_label"


def _duplicates(rows: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter(str(row.get(key, "")) for row in rows if str(row.get(key, "")))
    duplicate_keys = {item for item, count in counts.items() if count > 1}
    result: list[dict[str, Any]] = []
    for row in rows:
        value = str(row.get(key, ""))
        if value in duplicate_keys:
            result.append(
                {
                    "case": row.get("case", ""),
                    "source_id": row.get("source_id", ""),
                    "kind": row.get("kind", ""),
                    "env": row.get("env", ""),
                    "tex_path": row.get("tex_path", ""),
                    "char_start": row.get("char_start", 0),
                    "latex": str(row.get("latex", ""))[:160],
                }
            )
    return result


def _samples(rows: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    return rows[:limit]


def _candidate_samples(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    return [
        {
            "case": row.get("case", ""),
            "candidate_id": row.get("candidate_id", ""),
            "page_num": row.get("page_num", 0),
            "similarity": row.get("best_source_similarity", 0.0),
            "tier": _label_tier(row),
            "page_match": row.get("page_match", ""),
            "pdf_text": str(row.get("pdf_text", ""))[:160],
            "best_source_latex": str(row.get("best_source_latex", ""))[:160],
        }
        for row in rows[:limit]
    ]


def _status(source_empty: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> str:
    if source_empty:
        return "blocked_source_gold_not_clean"
    if not candidates:
        return "ok_source_only_no_pdf_candidates"
    return "ok_candidate_labels_tiered"


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


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    payload = audit_gold(args.dataset_dir)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if not payload["status"].startswith("blocked") else 1


if __name__ == "__main__":
    raise SystemExit(main())
