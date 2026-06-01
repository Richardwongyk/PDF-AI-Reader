"""Evaluate TinyBDMath structural candidates against relation labels.

This is an audit utility for model development.  It can compare candidate-only
selected relations with weak or future SLT/MathML relation labels, but it does
not decide accepted formula output.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any


EVAL_SCHEMA_VERSION = "tinybdmath_structural_eval_v1"
EVAL_LABELS = ("HORIZONTAL", "SUP", "SUB", "ABOVE", "BELOW", "FRACTION_BAR", "OVERLINE", "RADICAL_BODY")


def evaluate_structural_candidates(
    candidates: list[dict[str, Any]],
    relation_label_rows: list[dict[str, Any]],
    *,
    include_weak: bool = True,
) -> dict[str, Any]:
    labels_by_row = {str(row.get("row_id", "")): row for row in relation_label_rows}
    totals = {
        label: {"tp": 0, "fp": 0, "fn": 0}
        for label in EVAL_LABELS
    }
    row_reports: list[dict[str, Any]] = []
    warnings = Counter()
    for candidate in candidates:
        row_id = str(candidate.get("row_id", ""))
        label_row = labels_by_row.get(row_id, {})
        predicted = _selected_relation_set(candidate)
        expected = _label_relation_set(label_row, include_weak=include_weak)
        row_report = _row_report(row_id, predicted, expected, candidate)
        row_reports.append(row_report)
        warnings.update(candidate.get("verifier_warnings", []))
        for label in EVAL_LABELS:
            pred_edges = {edge for edge in predicted if edge[2] == label}
            gold_edges = {edge for edge in expected if edge[2] == label}
            totals[label]["tp"] += len(pred_edges & gold_edges)
            totals[label]["fp"] += len(pred_edges - gold_edges)
            totals[label]["fn"] += len(gold_edges - pred_edges)
    metrics = {label: _metrics(values) for label, values in totals.items()}
    micro_counts = Counter()
    for values in totals.values():
        micro_counts.update(values)
    return {
        "schema_version": EVAL_SCHEMA_VERSION,
        "candidate_only": True,
        "rows": len(candidates),
        "labeled_rows": len(relation_label_rows),
        "include_weak": bool(include_weak),
        "micro": _metrics(micro_counts),
        "per_relation": metrics,
        "warning_counts": dict(sorted(warnings.items())),
        "row_reports": row_reports,
        "notes": [
            "Metrics compare selected relation candidates with relation labels.",
            "Weak labels are development supervision only; this report is not product accuracy.",
            "Accepted formula quality still requires SLT/MathML hard labels, decoder, and verifier gates.",
        ],
    }


class TinyBDStructuralEvalAccumulator:
    def __init__(self, *, include_weak: bool = True) -> None:
        self.include_weak = bool(include_weak)
        self.totals = {
            label: {"tp": 0, "fp": 0, "fn": 0}
            for label in EVAL_LABELS
        }
        self.row_reports: list[dict[str, Any]] = []
        self.warnings: Counter[str] = Counter()
        self.labeled_rows = 0

    def add_pair(self, candidate: dict[str, Any], label_row: dict[str, Any]) -> None:
        row_id = str(candidate.get("row_id", ""))
        predicted = _selected_relation_set(candidate)
        expected = _label_relation_set(label_row, include_weak=self.include_weak)
        self.row_reports.append(_row_report(row_id, predicted, expected, candidate))
        self.warnings.update(candidate.get("verifier_warnings", []))
        if label_row:
            self.labeled_rows += 1
        for label in EVAL_LABELS:
            pred_edges = {edge for edge in predicted if edge[2] == label}
            gold_edges = {edge for edge in expected if edge[2] == label}
            self.totals[label]["tp"] += len(pred_edges & gold_edges)
            self.totals[label]["fp"] += len(pred_edges - gold_edges)
            self.totals[label]["fn"] += len(gold_edges - pred_edges)

    def to_report(self, *, streaming: bool = False) -> dict[str, Any]:
        metrics = {label: _metrics(values) for label, values in self.totals.items()}
        micro_counts = Counter()
        for values in self.totals.values():
            micro_counts.update(values)
        return {
            "schema_version": EVAL_SCHEMA_VERSION,
            "candidate_only": True,
            "rows": len(self.row_reports),
            "labeled_rows": self.labeled_rows,
            "include_weak": self.include_weak,
            "streaming": bool(streaming),
            "micro": _metrics(micro_counts),
            "per_relation": metrics,
            "warning_counts": dict(sorted(self.warnings.items())),
            "row_reports": self.row_reports,
            "notes": [
                "Metrics compare selected relation candidates with relation labels.",
                "Weak labels are development supervision only; this report is not product accuracy.",
                "Accepted formula quality still requires SLT/MathML hard labels, decoder, and verifier gates.",
            ],
        }


def evaluate_structural_candidates_stream(
    candidates_path: Path,
    relation_labels_path: Path,
    *,
    limit: int = 0,
    include_weak: bool = True,
) -> dict[str, Any]:
    accumulator = TinyBDStructuralEvalAccumulator(include_weak=include_weak)
    row_limit = int(limit or 0)
    with candidates_path.open("r", encoding="utf-8") as candidate_handle, relation_labels_path.open("r", encoding="utf-8") as label_handle:
        while row_limit <= 0 or len(accumulator.row_reports) < row_limit:
            candidate = _read_next_json_object(candidate_handle)
            label_row = _read_next_json_object(label_handle)
            if candidate is None or label_row is None:
                break
            if str(candidate.get("row_id", "")) != str(label_row.get("row_id", "")):
                candidate = dict(candidate)
                warnings = list(candidate.get("verifier_warnings", []) or [])
                warnings.append("stream_row_id_mismatch")
                candidate["verifier_warnings"] = warnings
                label_row = {}
            accumulator.add_pair(candidate, label_row)
    return accumulator.to_report(streaming=True)


def read_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
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


def write_eval_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _selected_relation_set(candidate: dict[str, Any]) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for item in candidate.get("selected_relations", []):
        if not isinstance(item, dict):
            continue
        relation = str(item.get("relation", ""))
        if relation in EVAL_LABELS:
            result.add((str(item.get("source", "")), str(item.get("target", "")), relation))
    return result


def _label_relation_set(label_row: dict[str, Any], *, include_weak: bool) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for item in label_row.get("edge_labels", []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        quality = str(item.get("quality", ""))
        if label not in EVAL_LABELS:
            continue
        if quality == "ignored":
            continue
        if quality == "weak" and not include_weak:
            continue
        result.add((str(item.get("source", "")), str(item.get("target", "")), label))
    return result


def _row_report(
    row_id: str,
    predicted: set[tuple[str, str, str]],
    expected: set[tuple[str, str, str]],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    tp = predicted & expected
    fp = predicted - expected
    fn = expected - predicted
    return {
        "row_id": row_id,
        "predicted": len(predicted),
        "expected": len(expected),
        "tp": len(tp),
        "fp": len(fp),
        "fn": len(fn),
        "f1": _metrics({"tp": len(tp), "fp": len(fp), "fn": len(fn)})["f1"],
        "abstain": bool(candidate.get("abstain")),
        "warnings": list(candidate.get("verifier_warnings", [])),
    }


def _metrics(values: dict[str, int] | Counter) -> dict[str, Any]:
    tp = int(values.get("tp", 0))
    fp = int(values.get("fp", 0))
    fn = int(values.get("fn", 0))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }
