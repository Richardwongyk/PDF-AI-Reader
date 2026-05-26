"""Train a dependency-light TinyBDMath edge baseline from graph rows and labels."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_edge_baseline import (
    EDGE_BASELINE_VERSION,
    EDGE_FEATURES,
    EDGE_LABELS,
    TinyBDEdgeBaselineModel,
    evaluate_edge_baseline,
    train_edge_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--relation-labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--training-mode", choices=("auto", "sgd", "hint-prior"), default="auto")
    args = parser.parse_args()

    edge_count = _edge_label_count(args.relation_labels, limit_rows=args.limit)
    mode = "hint-prior" if args.training_mode == "auto" and edge_count > 300000 else args.training_mode
    if mode == "hint-prior":
        model, report = _train_hint_prior_model(args.graph_rows, args.relation_labels, limit_rows=args.limit)
    else:
        samples, sample_stats = _samples_joined(args.graph_rows, args.relation_labels, limit_rows=args.limit)
        model, report = train_edge_baseline(samples, epochs=args.epochs)
        report["sample_selection"] = sample_stats
    report["training_mode_selected"] = mode

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save(args.output_dir / "tinybdmath_edge_baseline_model.json")
    (args.output_dir / "tinybdmath_edge_baseline_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _samples_joined(
    graph_rows: Path,
    relation_labels: Path,
    *,
    limit_rows: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    rows_seen = 0
    total_edges = 0
    matched_edges = 0
    with graph_rows.open("r", encoding="utf-8") as graph_handle, relation_labels.open("r", encoding="utf-8") as label_handle:
        for graph_line, label_line in zip(graph_handle, label_handle):
            graph_row = json.loads(graph_line)
            label_row = json.loads(label_line)
            if not isinstance(graph_row, dict) or not isinstance(label_row, dict):
                continue
            row_id = str(label_row.get("row_id", "") or graph_row.get("row_id", ""))
            edge_by_id = {
                str(edge.get("edge_id", "")): edge
                for edge in graph_row.get("candidate_edges", [])
                if isinstance(edge, dict)
            }
            for label in label_row.get("edge_labels", []):
                total_edges += 1
                if not isinstance(label, dict):
                    continue
                edge_id = str(label.get("edge_id", ""))
                edge = dict(edge_by_id.get(edge_id, {}))
                if not edge:
                    continue
                matched_edges += 1
                edge["row_id"] = row_id
                edge["edge_id"] = edge_id
                edge["label"] = str(label.get("label", ""))
                edge["quality"] = str(label.get("quality", ""))
                samples.append(edge)
            rows_seen += 1
            if limit_rows > 0 and rows_seen >= limit_rows:
                break
    return samples, {
        "method": "streaming_row_join_all_edges",
        "rows_seen": rows_seen,
        "edge_labels_seen": total_edges,
        "matched_edges": matched_edges,
        "samples_used": len(samples),
    }


def _train_hint_prior_model(
    graph_rows: Path,
    relation_labels: Path,
    *,
    limit_rows: int = 0,
) -> tuple[TinyBDEdgeBaselineModel, dict[str, Any]]:
    rows_seen = 0
    total_edges = 0
    matched_edges = 0
    usable_edges = 0
    label_counts: Counter[str] = Counter()
    hint_label_counts: Counter[tuple[str, str]] = Counter()
    eval_samples: list[dict[str, Any]] = []
    max_eval_samples = 100000
    with graph_rows.open("r", encoding="utf-8") as graph_handle, relation_labels.open("r", encoding="utf-8") as label_handle:
        for graph_line, label_line in zip(graph_handle, label_handle):
            graph_row = json.loads(graph_line)
            label_row = json.loads(label_line)
            if not isinstance(graph_row, dict) or not isinstance(label_row, dict):
                continue
            row_id = str(label_row.get("row_id", "") or graph_row.get("row_id", ""))
            edge_by_id = {
                str(edge.get("edge_id", "")): edge
                for edge in graph_row.get("candidate_edges", [])
                if isinstance(edge, dict)
            }
            for label in label_row.get("edge_labels", []):
                total_edges += 1
                if not isinstance(label, dict):
                    continue
                relation = str(label.get("label", ""))
                if relation not in EDGE_LABELS:
                    continue
                edge_id = str(label.get("edge_id", ""))
                edge = dict(edge_by_id.get(edge_id, {}))
                if not edge:
                    continue
                matched_edges += 1
                hint_key = _hint_feature(str(edge.get("hint", "")))
                if not hint_key:
                    continue
                usable_edges += 1
                label_counts[relation] += 1
                hint_label_counts[(hint_key, relation)] += 1
                if len(eval_samples) < max_eval_samples:
                    edge["row_id"] = row_id
                    edge["edge_id"] = edge_id
                    edge["label"] = relation
                    eval_samples.append(edge)
            rows_seen += 1
            if limit_rows > 0 and rows_seen >= limit_rows:
                break

    weights = [[0.0 for _ in EDGE_FEATURES] for _ in EDGE_LABELS]
    feature_to_index = {name: index for index, name in enumerate(EDGE_FEATURES)}
    label_to_index = {label: index for index, label in enumerate(EDGE_LABELS)}
    total_labels = sum(label_counts.values()) or 1
    for label in EDGE_LABELS:
        prior = (label_counts.get(label, 0) + 1.0) / (total_labels + len(EDGE_LABELS))
        weights[label_to_index[label]][feature_to_index["bias"]] = round(prior, 8)
    for feature_name in (
        "hint_right_neighbor",
        "hint_superscript_zone",
        "hint_subscript_zone",
        "hint_above_zone",
        "hint_below_zone",
        "hint_far_context",
        "hint_rule",
        "hint_above_rule",
        "hint_below_rule",
        "hint_fraction_bar",
        "hint_overline",
        "hint_radical_body",
    ):
        total = sum(hint_label_counts.get((feature_name, label), 0) for label in EDGE_LABELS)
        if total <= 0:
            continue
        for label in EDGE_LABELS:
            probability = (hint_label_counts.get((feature_name, label), 0) + 1.0) / (total + len(EDGE_LABELS))
            weights[label_to_index[label]][feature_to_index[feature_name]] = round(6.0 * probability, 8)

    model = TinyBDEdgeBaselineModel(
        version=EDGE_BASELINE_VERSION,
        feature_names=EDGE_FEATURES,
        labels=EDGE_LABELS,
        weights=tuple(tuple(row) for row in weights),
        means=tuple(0.0 for _ in EDGE_FEATURES),
        scales=tuple(1.0 for _ in EDGE_FEATURES),
        train_config={
            "mode": "hint_prior_full_stream",
            "rows_seen": rows_seen,
            "edge_labels_seen": total_edges,
            "matched_edges": matched_edges,
            "usable_edges": usable_edges,
        },
    )
    report = {
        "schema_version": "tinybdmath_edge_baseline_report_v1",
        "model_version": EDGE_BASELINE_VERSION,
        "samples": usable_edges,
        "train_samples": usable_edges,
        "validation_samples": len(eval_samples),
        "train": {"samples": usable_edges, "accuracy": None, "confusion": {}},
        "validation": evaluate_edge_baseline(model, eval_samples),
        "label_counts": {label: int(label_counts.get(label, 0)) for label in EDGE_LABELS},
        "hint_label_counts": {f"{hint}:{label}": count for (hint, label), count in sorted(hint_label_counts.items())},
        "sample_selection": {
            "method": "full_stream_hint_prior_no_sampling",
            "rows_seen": rows_seen,
            "edge_labels_seen": total_edges,
            "matched_edges": matched_edges,
            "usable_edges": usable_edges,
            "eval_samples": len(eval_samples),
        },
        "warning": "Trained on weak relation labels; use as candidate evidence only.",
    }
    return model, report


def _hint_feature(hint: str) -> str:
    if hint == "above_rule_candidate":
        return "hint_above_rule"
    if hint == "below_rule_candidate":
        return "hint_below_rule"
    if hint == "fraction_bar_candidate":
        return "hint_fraction_bar"
    if hint == "overline_candidate":
        return "hint_overline"
    if hint == "radical_body_candidate":
        return "hint_radical_body"
    if hint == "right_neighbor":
        return "hint_right_neighbor"
    if hint == "superscript_zone":
        return "hint_superscript_zone"
    if hint == "subscript_zone":
        return "hint_subscript_zone"
    if hint in {"above_zone", "above_rule_candidate"}:
        return "hint_above_zone"
    if hint in {"below_zone", "below_rule_candidate"}:
        return "hint_below_zone"
    if hint == "far_context":
        return "hint_far_context"
    if "rule" in hint or "fraction_bar" in hint:
        return "hint_rule"
    return ""


def _edge_label_count(path: Path, *, limit_rows: int = 0) -> int:
    total = 0
    rows_seen = 0
    for row in _read_jsonl(path):
        labels = row.get("edge_labels", [])
        total += len(labels) if isinstance(labels, list) else 0
        rows_seen += 1
        if limit_rows > 0 and rows_seen >= limit_rows:
            break
    return total


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                yield value


if __name__ == "__main__":
    raise SystemExit(main())
