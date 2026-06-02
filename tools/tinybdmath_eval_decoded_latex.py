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

from src.core.tinybdmath_latex_decoder import decode_latex_candidate
from tools.formula_latex_audit import _formula_similarity, _normalize_formula_for_match


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate TinyBDMath decoded LaTeX candidates against graph-row labels.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True, help="Graph Parser structural candidate JSONL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Evaluate rows incrementally by joining graph rows and candidates in file order.",
    )
    args = parser.parse_args()

    if args.stream:
        decoded_rows, warnings = _decode_rows_stream(
            args.graph_rows,
            args.candidates,
            limit=args.limit,
        )
    else:
        graph_rows = _read_jsonl(args.graph_rows, limit=args.limit)
        candidates = _read_jsonl(args.candidates, limit=args.limit)
        rows_by_id = {str(row.get("row_id", "")): row for row in graph_rows}
        decoded_rows, warnings = _decode_rows(candidates, rows_by_id)
    report = _build_report(decoded_rows, warnings, streaming=bool(args.stream))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows_path = args.output.with_suffix(".jsonl")
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in decoded_rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"schema_version": report["schema_version"], "rows": report["rows"], **report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


def _decode_rows(
    candidates: list[dict[str, Any]],
    rows_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    decoded_rows: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    for candidate in candidates:
        row_id = str(candidate.get("row_id", ""))
        graph_row = rows_by_id.get(row_id, {})
        decoded = decode_latex_candidate(
            list(graph_row.get("glyph_nodes", []) or graph_row.get("glyphs", []) or []),
            candidate,
            vectors=list(graph_row.get("vector_nodes", []) or graph_row.get("vectors", []) or []),
            fallback_text=_fallback_text(graph_row),
        ).to_json()
        label = str(graph_row.get("label_latex", "") or "")
        similarity = _similarity(label, str(decoded.get("latex", "") or ""))
        warnings.update(str(item) for item in decoded.get("warnings", []) if item)
        decoded_rows.append(
            {
                "row_id": row_id,
                "kind": str(graph_row.get("kind", "") or candidate.get("kind", "")),
                "label_latex": label,
                "decoded_latex": str(decoded.get("latex", "") or ""),
                "similarity": round(similarity, 6),
                "decoded_confidence": float(decoded.get("confidence", 0.0) or 0.0),
                "candidate_abstain": bool(candidate.get("abstain")),
                "warnings": list(decoded.get("warnings", []) or []),
            }
        )
    return decoded_rows, warnings


def _decode_rows_stream(
    graph_rows_path: Path,
    candidates_path: Path,
    *,
    limit: int = 0,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    decoded_rows: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    row_limit = int(limit or 0)
    with graph_rows_path.open("r", encoding="utf-8") as graph_handle, candidates_path.open("r", encoding="utf-8") as candidate_handle:
        while row_limit <= 0 or len(decoded_rows) < row_limit:
            graph_row = _read_next_json_object(graph_handle)
            candidate = _read_next_json_object(candidate_handle)
            if graph_row is None or candidate is None:
                break
            row_id = str(candidate.get("row_id", ""))
            if str(graph_row.get("row_id", "")) != row_id:
                graph_row = {}
                warnings.update(["stream_row_id_mismatch"])
            decoded = decode_latex_candidate(
                list(graph_row.get("glyph_nodes", []) or graph_row.get("glyphs", []) or []),
                candidate,
                vectors=list(graph_row.get("vector_nodes", []) or graph_row.get("vectors", []) or []),
                fallback_text=_fallback_text(graph_row),
            ).to_json()
            label = str(graph_row.get("label_latex", "") or "")
            similarity = _similarity(label, str(decoded.get("latex", "") or ""))
            warnings.update(str(item) for item in decoded.get("warnings", []) if item)
            decoded_rows.append(
                {
                    "row_id": row_id,
                    "kind": str(graph_row.get("kind", "") or candidate.get("kind", "")),
                    "label_latex": label,
                    "decoded_latex": str(decoded.get("latex", "") or ""),
                    "similarity": round(similarity, 6),
                    "decoded_confidence": float(decoded.get("confidence", 0.0) or 0.0),
                    "candidate_abstain": bool(candidate.get("abstain")),
                    "warnings": list(decoded.get("warnings", []) or []),
                }
            )
    return decoded_rows, warnings


def _build_report(decoded_rows: list[dict[str, Any]], warnings: Counter[str], *, streaming: bool) -> dict[str, Any]:
    metrics = _metrics(decoded_rows)
    return {
        "schema_version": "tinybdmath_decoded_latex_eval_v1",
        "rows": len(decoded_rows),
        "metrics": metrics,
        "warning_counts": dict(sorted(warnings.items())),
        "streaming": bool(streaming),
        "sample_low_similarity": [
            {
                "row_id": row["row_id"],
                "label_latex": row["label_latex"],
                "decoded_latex": row["decoded_latex"],
                "similarity": row["similarity"],
                "warnings": row["warnings"][:8],
            }
            for row in sorted(decoded_rows, key=lambda item: float(item["similarity"]))[:10]
        ],
        "candidate_only": True,
        "accepted_latex_emitted": False,
        "notes": [
            "This evaluates the final TinyBDMath decoded candidate, not only relation F1.",
            "Labels come from instrumented training/audit graph rows and are not available in production.",
        ],
    }


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


def _read_next_json_object(handle: Any) -> dict[str, Any] | None:
    for line in handle:
        text = line.strip()
        if not text:
            continue
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    return None


def _fallback_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for glyph in row.get("glyph_nodes", []) or row.get("glyphs", []) or []:
        if not isinstance(glyph, dict):
            continue
        parts.append(str(glyph.get("unicode", "") or glyph.get("text", "") or ""))
    return "".join(parts)


def _similarity(expected: str, actual: str) -> float:
    expected_compact = _compact_latex(expected)
    actual_compact = _compact_latex(actual)
    if expected_compact and actual_compact and expected_compact == actual_compact:
        return 1.0
    left = _normalize_formula_for_match(expected)
    right = _normalize_formula_for_match(actual)
    if not left or not right:
        return 0.0
    return _formula_similarity(left, right)


def _compact_latex(value: str) -> str:
    return "".join(str(value or "").split())


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [float(row.get("similarity", 0.0) or 0.0) for row in rows]
    if not scored:
        return {
            "exact_match_rate": 0.0,
            "near_match_rate": 0.0,
            "weak_match_rate": 0.0,
            "average_similarity": 0.0,
            "decoded_nonempty_rate": 0.0,
        }
    return {
        "exact_match_rate": round(sum(1 for value in scored if value >= 0.98) / len(scored), 6),
        "near_match_rate": round(sum(1 for value in scored if value >= 0.80) / len(scored), 6),
        "weak_match_rate": round(sum(1 for value in scored if value >= 0.55) / len(scored), 6),
        "average_similarity": round(sum(scored) / len(scored), 6),
        "decoded_nonempty_rate": round(sum(1 for row in rows if str(row.get("decoded_latex", "") or "").strip()) / len(rows), 6),
    }


if __name__ == "__main__":
    raise SystemExit(main())
