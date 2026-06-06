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

from src.core.latex_mathml_extractor import read_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit TinyBDMath CSLT alignment rows.")
    parser.add_argument("--alignment-rows", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-hard-row-rate", type=float, default=0.70)
    args = parser.parse_args()

    rows = read_jsonl(args.alignment_rows, limit=args.limit)
    report = audit_alignment_rows(rows, min_hard_row_rate=args.min_hard_row_rate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["gate"]["passed"] else 1


def audit_alignment_rows(rows: list[dict[str, Any]], *, min_hard_row_rate: float = 0.70) -> dict[str, Any]:
    warnings: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    structure_counts: Counter[str] = Counter()
    vector_role_counts: Counter[str] = Counter()
    supervision_counts: Counter[str] = Counter()
    ignored_reasons: Counter[str] = Counter()
    unmatched_reasons: Counter[str] = Counter()
    hard_rates: list[float] = []
    leaf_rates: list[float] = []
    rows_with_hard = 0
    rows_with_relation_labels = 0
    rows_with_structure_labels = 0
    rows_with_group_boundary = 0
    rows_with_text_or_operator_run = 0
    rows_with_vector_role = 0
    rows_with_identity_evidence = 0
    top_failures: list[dict[str, Any]] = []
    for row in rows:
        row_warnings = [str(item) for item in row.get("warnings", []) if item]
        warnings.update(row_warnings)
        stats = row.get("stats", {}) if isinstance(row.get("stats"), dict) else {}
        hard_rate = _float(stats.get("hard_alignment_rate"))
        leaf_rate = _float(stats.get("leaf_alignment_rate"))
        hard_rates.append(hard_rate)
        leaf_rates.append(leaf_rate)
        if hard_rate >= min_hard_row_rate:
            rows_with_hard += 1
        labels = [item for item in row.get("relation_labels", []) or [] if isinstance(item, dict)]
        if labels:
            rows_with_relation_labels += 1
        relation_counts.update(str(item.get("relation", "") or "") for item in labels)
        supervision_counts.update(str(item.get("supervision", "") or "") for item in labels)
        structure_labels = [item for item in row.get("structure_labels", []) or [] if isinstance(item, dict)]
        if structure_labels:
            rows_with_structure_labels += 1
        structure_roles = {str(item.get("role", "") or "") for item in structure_labels}
        structure_counts.update(str(item.get("role", "") or "") for item in structure_labels)
        vector_role_counts.update(
            str(item.get("vector_role", "") or "")
            for item in structure_labels
            if str(item.get("role", "") or "") == "TARGET_VECTOR_ROLE_EVIDENCE"
        )
        if any("GROUP_BOUNDARY" in role for role in structure_roles):
            rows_with_group_boundary += 1
        if structure_roles.intersection({"TARGET_TEXT_RUN_EVIDENCE", "TARGET_OPERATOR_TEXT_RUN_EVIDENCE"}):
            rows_with_text_or_operator_run += 1
        if "TARGET_VECTOR_ROLE_EVIDENCE" in structure_roles:
            rows_with_vector_role += 1
        if "TARGET_IDENTITY_REPAIR_EVIDENCE" in structure_roles:
            rows_with_identity_evidence += 1
        ignored_reasons.update(
            str(item.get("reason", "") or "")
            for item in row.get("ignored_pdf_nodes", []) or []
            if isinstance(item, dict)
        )
        unmatched_reasons.update(
            str(item.get("reason", "") or "")
            for item in row.get("unmatched_target_nodes", []) or []
            if isinstance(item, dict)
        )
        if row_warnings or hard_rate < min_hard_row_rate or stats.get("unmatched_target_nodes", 0):
            top_failures.append(
                {
                    "row_id": row.get("row_id", ""),
                    "hard_alignment_rate": hard_rate,
                    "leaf_alignment_rate": leaf_rate,
                    "warnings": row_warnings[:8],
                    "unmatched_target_nodes": row.get("unmatched_target_nodes", [])[:8],
                    "ignored_pdf_nodes": row.get("ignored_pdf_nodes", [])[:8],
                    "relation_counts": stats.get("relation_counts", {}),
                }
            )
    row_count = len(rows)
    hard_row_rate = rows_with_hard / row_count if row_count else 0.0
    relation_row_rate = rows_with_relation_labels / row_count if row_count else 0.0
    structure_row_rate = rows_with_structure_labels / row_count if row_count else 0.0
    gate_failures: list[str] = []
    if row_count <= 0:
        gate_failures.append("no_alignment_rows")
    if hard_row_rate < min_hard_row_rate:
        gate_failures.append(f"hard_row_rate {hard_row_rate:.3f} < {min_hard_row_rate:.3f}")
    if relation_row_rate <= 0:
        gate_failures.append("no_relation_label_rows")
    return {
        "schema_version": "tinybdmath_alignment_audit_v1",
        "rows": row_count,
        "rows_with_hard_alignment": rows_with_hard,
        "rows_with_relation_labels": rows_with_relation_labels,
        "rows_with_structure_labels": rows_with_structure_labels,
        "rows_with_group_boundary": rows_with_group_boundary,
        "rows_with_text_or_operator_run": rows_with_text_or_operator_run,
        "rows_with_vector_role": rows_with_vector_role,
        "rows_with_identity_evidence": rows_with_identity_evidence,
        "hard_row_rate": round(hard_row_rate, 6),
        "relation_row_rate": round(relation_row_rate, 6),
        "structure_row_rate": round(structure_row_rate, 6),
        "avg_hard_alignment_rate": round(sum(hard_rates) / row_count, 6) if row_count else 0.0,
        "avg_leaf_alignment_rate": round(sum(leaf_rates) / row_count, 6) if row_count else 0.0,
        "warnings": dict(sorted(warnings.items())),
        "relation_counts": dict(sorted(relation_counts.items())),
        "structure_counts": dict(sorted(structure_counts.items())),
        "vector_role_counts": dict(sorted(vector_role_counts.items())),
        "supervision_counts": dict(sorted(supervision_counts.items())),
        "ignored_reasons": dict(sorted(ignored_reasons.items())),
        "unmatched_reasons": dict(sorted(unmatched_reasons.items())),
        "top_failures": sorted(
            top_failures,
            key=lambda item: (float(item.get("hard_alignment_rate", 0.0)), len(item.get("warnings", []))),
        )[:20],
        "gate": {
            "passed": not gate_failures,
            "min_hard_row_rate": min_hard_row_rate,
            "failures": gate_failures,
        },
        "notes": [
            "This audits training labels, not production formula quality.",
            "Failed rows should be fixed in target tree/alignment or ignored, not patched in decoder.",
        ],
    }


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
