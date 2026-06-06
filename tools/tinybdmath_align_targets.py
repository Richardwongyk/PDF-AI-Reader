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

from src.core.tinybdmath_alignment import ALIGNMENT_BUILDER_VERSION, TinyBDAlignmentBuilder


def main() -> int:
    parser = argparse.ArgumentParser(description="Align TinyBDMath graph rows to CSLT target trees.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--target-trees", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=5000)
    args = parser.parse_args()

    builder = TinyBDAlignmentBuilder()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    warnings: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    structure_counts: Counter[str] = Counter()
    vector_role_counts: Counter[str] = Counter()
    hard_rates: list[float] = []
    rows = 0
    rows_with_hard_labels = 0
    rows_with_structure_labels = 0
    rows_with_group_boundary = 0
    rows_with_text_or_operator_run = 0
    rows_with_vector_role = 0
    rows_with_identity_evidence = 0
    with (args.output_dir / "tinybdmath_alignment_rows.jsonl").open("w", encoding="utf-8") as handle:
        for graph_row, target_row in _iter_aligned_input_rows(args.graph_rows, args.target_trees, limit=args.limit):
            result = builder.align_row(graph_row, target_row)
            payload = result.to_json()
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            rows += 1
            warnings.update(str(item) for item in result.warnings if item)
            relation_counts.update(str(item.get("relation", "")) for item in payload.get("relation_labels", []) if item)
            structure_labels = [item for item in payload.get("structure_labels", []) if isinstance(item, dict)]
            structure_counts.update(str(item.get("role", "")) for item in structure_labels if item)
            vector_role_counts.update(
                str(item.get("vector_role", "") or "")
                for item in structure_labels
                if str(item.get("role", "") or "") == "TARGET_VECTOR_ROLE_EVIDENCE"
            )
            structure_roles = {str(item.get("role", "") or "") for item in structure_labels}
            hard_rates.append(float(payload.get("stats", {}).get("hard_alignment_rate", 0.0)))
            if payload.get("relation_labels"):
                rows_with_hard_labels += 1
            if payload.get("structure_labels"):
                rows_with_structure_labels += 1
            if any("GROUP_BOUNDARY" in role for role in structure_roles):
                rows_with_group_boundary += 1
            if structure_roles.intersection({"TARGET_TEXT_RUN_EVIDENCE", "TARGET_OPERATOR_TEXT_RUN_EVIDENCE"}):
                rows_with_text_or_operator_run += 1
            if "TARGET_VECTOR_ROLE_EVIDENCE" in structure_roles:
                rows_with_vector_role += 1
            if "TARGET_IDENTITY_REPAIR_EVIDENCE" in structure_roles:
                rows_with_identity_evidence += 1
            if args.progress_every > 0 and rows % max(1, int(args.progress_every)) == 0:
                print(
                    json.dumps(
                        {
                            "event": "alignment_progress",
                            "rows": rows,
                            "rows_with_hard_labels": rows_with_hard_labels,
                            "rows_with_structure_labels": rows_with_structure_labels,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    manifest = {
        "schema_version": "tinybdmath_alignment_manifest_v1",
        "alignment_version": ALIGNMENT_BUILDER_VERSION,
        "rows": rows,
        "rows_with_hard_labels": rows_with_hard_labels,
        "rows_with_structure_labels": rows_with_structure_labels,
        "rows_with_group_boundary": rows_with_group_boundary,
        "rows_with_text_or_operator_run": rows_with_text_or_operator_run,
        "rows_with_vector_role": rows_with_vector_role,
        "rows_with_identity_evidence": rows_with_identity_evidence,
        "avg_hard_alignment_rate": round(sum(hard_rates) / len(hard_rates), 6) if hard_rates else 0.0,
        "warnings": dict(sorted(warnings.items())),
        "relation_counts": dict(sorted(relation_counts.items())),
        "structure_counts": dict(sorted(structure_counts.items())),
        "vector_role_counts": dict(sorted(vector_role_counts.items())),
        "streaming": True,
        "notes": [
            "Alignment labels are training/audit supervision.",
            "Low-confidence or unmatched nodes are ignore labels, not decoder repairs.",
        ],
    }
    (args.output_dir / "tinybdmath_alignment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def _iter_aligned_input_rows(
    graph_rows_path: Path,
    target_rows_path: Path,
    *,
    limit: int = 0,
) -> Any:
    row_limit = int(limit or 0)
    count = 0
    with graph_rows_path.open("r", encoding="utf-8") as graph_handle, target_rows_path.open("r", encoding="utf-8") as target_handle:
        while row_limit <= 0 or count < row_limit:
            graph_row = _read_next_json_object(graph_handle)
            target_row = _read_next_json_object(target_handle)
            if graph_row is None or target_row is None:
                break
            yield graph_row, target_row
            count += 1


def _read_next_json_object(handle: Any) -> dict[str, Any] | None:
    for line in handle:
        text = line.strip()
        if not text:
            continue
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    return None


if __name__ == "__main__":
    raise SystemExit(main())
