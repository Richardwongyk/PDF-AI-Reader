from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.latex_mathml_extractor import read_jsonl
from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_ARTIFACT_VERSION,
    GRAPH_PARSER_FEATURE_VERSION,
    GRAPH_PARSER_FEATURES,
    GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD,
    GRAPH_PARSER_NODE_FEATURES,
    GRAPH_PARSER_NODE_LABELS,
    GRAPH_PARSER_RELATIONS,
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
    node_training_samples_from_rows,
    training_samples_from_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train TinyBDMath Graph Parser M1 with PyTorch.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--alignment-rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--hidden-units", type=int, default=128)
    parser.add_argument("--hidden-layers", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--node-filter-threshold", type=float, default=GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD)
    parser.add_argument("--seed", type=int, default=20260601)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "torch_unavailable", "detail": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    graph_rows = read_jsonl(args.graph_rows, limit=args.limit)
    alignment_rows = read_jsonl(args.alignment_rows, limit=args.limit)
    samples = training_samples_from_rows(graph_rows, alignment_rows)
    node_samples = node_training_samples_from_rows(graph_rows, alignment_rows)
    if not samples:
        raise SystemExit("no graph parser samples")
    if not node_samples:
        raise SystemExit("no graph parser node samples")
    train_samples, validation_samples = _split(samples, validation_fraction=args.validation_fraction, seed=args.seed)
    train_node_samples, validation_node_samples = _split(
        node_samples,
        validation_fraction=args.validation_fraction,
        seed=args.seed,
    )
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    train_x, train_y, train_weight, means, scales = _tensorize(
        train_samples,
        feature_names=GRAPH_PARSER_FEATURES,
        labels=GRAPH_PARSER_RELATIONS,
        label_key="relation",
        torch=torch,
    )
    _val_x, _val_y, _val_weight, _means, _scales = _tensorize(
        validation_samples,
        feature_names=GRAPH_PARSER_FEATURES,
        labels=GRAPH_PARSER_RELATIONS,
        label_key="relation",
        torch=torch,
        means=means,
        scales=scales,
    )
    train_node_x, train_node_y, train_node_weight, node_means, node_scales = _tensorize(
        train_node_samples,
        feature_names=GRAPH_PARSER_NODE_FEATURES,
        labels=GRAPH_PARSER_NODE_LABELS,
        label_key="label",
        torch=torch,
    )
    _val_node_x, _val_node_y, _val_node_weight, _node_means, _node_scales = _tensorize(
        validation_node_samples,
        feature_names=GRAPH_PARSER_NODE_FEATURES,
        labels=GRAPH_PARSER_NODE_LABELS,
        label_key="label",
        torch=torch,
        means=node_means,
        scales=node_scales,
    )

    model = _build_mlp(
        input_width=len(GRAPH_PARSER_FEATURES),
        output_width=len(GRAPH_PARSER_RELATIONS),
        hidden_units=int(args.hidden_units),
        hidden_layers=int(args.hidden_layers),
        nn=nn,
    ).to(args.device)
    node_model = _build_mlp(
        input_width=len(GRAPH_PARSER_NODE_FEATURES),
        output_width=len(GRAPH_PARSER_NODE_LABELS),
        hidden_units=max(8, int(args.hidden_units) // 2),
        hidden_layers=max(1, int(args.hidden_layers)),
        nn=nn,
    ).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    node_optimizer = torch.optim.AdamW(node_model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    class_weights = _class_weights(train_y, classes=len(GRAPH_PARSER_RELATIONS), torch=torch, device=args.device)
    node_class_weights = _node_class_weights(train_node_y, torch=torch, device=args.device)
    loss_fn = nn.CrossEntropyLoss(weight=class_weights, reduction="none")
    node_loss_fn = nn.CrossEntropyLoss(weight=node_class_weights, reduction="none")
    loader = DataLoader(TensorDataset(train_x, train_y, train_weight), batch_size=max(1, int(args.batch_size)), shuffle=True)
    node_loader = DataLoader(TensorDataset(train_node_x, train_node_y, train_node_weight), batch_size=max(1, int(args.batch_size)), shuffle=True)
    for _epoch in range(max(1, int(args.epochs))):
        model.train()
        for batch_x, batch_y, batch_weight in loader:
            batch_x = batch_x.to(args.device)
            batch_y = batch_y.to(args.device)
            batch_weight = batch_weight.to(args.device)
            optimizer.zero_grad(set_to_none=True)
            loss = _weighted_loss(loss_fn(model(batch_x), batch_y), batch_weight, torch=torch)
            loss.backward()
            optimizer.step()
        node_model.train()
        for batch_x, batch_y, batch_weight in node_loader:
            batch_x = batch_x.to(args.device)
            batch_y = batch_y.to(args.device)
            batch_weight = batch_weight.to(args.device)
            node_optimizer.zero_grad(set_to_none=True)
            loss = _weighted_loss(node_loss_fn(node_model(batch_x), batch_y), batch_weight, torch=torch)
            loss.backward()
            node_optimizer.step()

    artifact = _export_artifact(model, node_model, means, scales, node_means, node_scales, args, torch=torch)
    train_eval = _evaluate_artifact(artifact, train_samples[:100000])
    validation_eval = _evaluate_artifact(artifact, validation_samples)
    node_train_eval = _evaluate_node_artifact(artifact, train_node_samples[:100000])
    node_validation_eval = _evaluate_node_artifact(artifact, validation_node_samples)
    report = {
        "schema_version": "tinybdmath_graph_parser_train_report_v1",
        "artifact_version": GRAPH_PARSER_ARTIFACT_VERSION,
        "model_version": artifact.model_version,
        "samples": len(samples),
        "node_samples": len(node_samples),
        "train_samples": len(train_samples),
        "validation_samples": len(validation_samples),
        "train_node_samples": len(train_node_samples),
        "validation_node_samples": len(validation_node_samples),
        "label_counts": dict(sorted(Counter(str(item["relation"]) for item in samples).items())),
        "node_label_counts": dict(sorted(Counter(str(item["label"]) for item in node_samples).items())),
        "weighted_label_counts": _weighted_label_counts(samples, label_key="relation"),
        "node_weighted_label_counts": _weighted_label_counts(node_samples, label_key="label"),
        "weight_summary": _weight_summary(samples),
        "node_weight_summary": _weight_summary(node_samples),
        "node_class_weights": _class_weight_report(node_class_weights, GRAPH_PARSER_NODE_LABELS),
        "train": train_eval,
        "validation": validation_eval,
        "node_train": node_train_eval,
        "node_validation": node_validation_eval,
        "train_config": artifact.train_config,
        "notes": [
            "PyTorch is used only in the isolated training environment.",
            "Exported Graph Parser artifact is candidate-only until verifier/accepted gate passes.",
            "Node labels train keep/drop behavior; decoder must not guess spacing or artifact handling.",
            "Node UNKNOWN labels are weak evidence, so node training uses confidence weights without class-balancing UNKNOWN upward.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    artifact.save(args.output_dir / "tinybdmath_graph_parser_model.json")
    (args.output_dir / "tinybdmath_graph_parser_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _build_mlp(*, input_width: int, output_width: int, hidden_units: int, hidden_layers: int, nn: Any) -> Any:
    layers: list[Any] = []
    hidden_width = max(8, int(hidden_units))
    for layer_index in range(max(1, int(hidden_layers))):
        layers.append(nn.Linear(input_width if layer_index == 0 else hidden_width, hidden_width))
        layers.append(nn.ReLU())
    layers.append(nn.Linear(hidden_width, output_width))
    return nn.Sequential(*layers)


def _export_artifact(
    model: Any,
    node_model: Any,
    means: list[float],
    scales: list[float],
    node_means: list[float],
    node_scales: list[float],
    args: argparse.Namespace,
    *,
    torch: Any,
) -> TinyBDGraphParserArtifact:
    from torch import nn

    linear_layers = [layer for layer in model if isinstance(layer, nn.Linear)]
    node_linear_layers = [layer for layer in node_model if isinstance(layer, nn.Linear)]
    hidden_layers = linear_layers[:-1]
    output = linear_layers[-1]
    node_hidden_layers = node_linear_layers[:-1]
    node_output = node_linear_layers[-1]
    return TinyBDGraphParserArtifact(
        version=GRAPH_PARSER_ARTIFACT_VERSION,
        model_version="tinybdmath_graph_parser_m1",
        feature_version=GRAPH_PARSER_FEATURE_VERSION,
        feature_names=GRAPH_PARSER_FEATURES,
        relation_labels=GRAPH_PARSER_RELATIONS,
        means=tuple(round(float(value), 8) for value in means),
        scales=tuple(round(float(value), 8) for value in scales),
        hidden_weights=tuple(
            tuple(tuple(round(float(value), 8) for value in row) for row in layer.weight.detach().cpu().tolist())
            for layer in hidden_layers
        ),
        hidden_biases=tuple(
            tuple(round(float(value), 8) for value in layer.bias.detach().cpu().tolist())
            for layer in hidden_layers
        ),
        output_weights=tuple(tuple(round(float(value), 8) for value in row) for row in output.weight.detach().cpu().tolist()),
        output_bias=tuple(round(float(value), 8) for value in output.bias.detach().cpu().tolist()),
        threshold=float(args.threshold),
        train_config={
            "mode": "graph_parser_m1",
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "hidden_units": int(args.hidden_units),
            "hidden_layers": int(args.hidden_layers),
            "learning_rate": float(args.learning_rate),
            "weight_decay": float(args.weight_decay),
            "validation_fraction": float(args.validation_fraction),
            "threshold": float(args.threshold),
            "node_filter_threshold": float(args.node_filter_threshold),
            "seed": int(args.seed),
            "device": str(args.device),
            "torch_version": str(torch.__version__),
        },
        node_feature_names=GRAPH_PARSER_NODE_FEATURES,
        node_label_names=GRAPH_PARSER_NODE_LABELS,
        node_means=tuple(round(float(value), 8) for value in node_means),
        node_scales=tuple(round(float(value), 8) for value in node_scales),
        node_hidden_weights=tuple(
            tuple(tuple(round(float(value), 8) for value in row) for row in layer.weight.detach().cpu().tolist())
            for layer in node_hidden_layers
        ),
        node_hidden_biases=tuple(
            tuple(round(float(value), 8) for value in layer.bias.detach().cpu().tolist())
            for layer in node_hidden_layers
        ),
        node_output_weights=tuple(tuple(round(float(value), 8) for value in row) for row in node_output.weight.detach().cpu().tolist()),
        node_output_bias=tuple(round(float(value), 8) for value in node_output.bias.detach().cpu().tolist()),
        node_filter_threshold=float(args.node_filter_threshold),
    )


def _tensorize(
    samples: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    labels: tuple[str, ...],
    label_key: str,
    torch: Any,
    means: list[float] | None = None,
    scales: list[float] | None = None,
) -> tuple[Any, Any, Any, list[float], list[float]]:
    raw = [[float(sample.get("features", {}).get(name, 0.0) or 0.0) for name in feature_names] for sample in samples]
    if means is None:
        means = [
            sum(row[index] for row in raw) / max(1, len(raw))
            for index in range(len(feature_names))
        ]
    if scales is None:
        scales = []
        for index, mean in enumerate(means):
            variance = sum((row[index] - mean) ** 2 for row in raw) / max(1, len(raw))
            scales.append(max(1e-6, variance ** 0.5))
    normalized = [
        [
            (row[index] - means[index]) / (scales[index] if abs(scales[index]) > 1e-12 else 1.0)
            for index in range(len(feature_names))
        ]
        for row in raw
    ]
    label_to_index = {label: index for index, label in enumerate(labels)}
    label_values = [label_to_index.get(str(sample.get(label_key, labels[0])), 0) for sample in samples]
    weights = [_sample_weight(sample) for sample in samples]
    return (
        torch.tensor(normalized, dtype=torch.float32),
        torch.tensor(label_values, dtype=torch.long),
        torch.tensor(weights, dtype=torch.float32),
        means,
        scales,
    )


def _sample_weight(sample: dict[str, Any]) -> float:
    try:
        value = float(sample.get("confidence", 1.0) or 0.0)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.01, min(1.0, value))


def _weighted_loss(losses: Any, weights: Any, *, torch: Any) -> Any:
    return (losses * weights).sum() / torch.clamp(weights.sum(), min=1e-6)


def _weighted_label_counts(samples: list[dict[str, Any]], *, label_key: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for sample in samples:
        label = str(sample.get(label_key, "") or "")
        totals[label] = totals.get(label, 0.0) + _sample_weight(sample)
    return {label: round(value, 6) for label, value in sorted(totals.items())}


def _weight_summary(samples: list[dict[str, Any]]) -> dict[str, float]:
    weights = [_sample_weight(sample) for sample in samples]
    if not weights:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "min": round(min(weights), 6),
        "max": round(max(weights), 6),
        "mean": round(sum(weights) / len(weights), 6),
    }


def _split(samples: list[dict[str, Any]], *, validation_fraction: float, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    shuffled = list(samples)
    random.Random(seed).shuffle(shuffled)
    validation_count = max(1, int(len(shuffled) * max(0.0, min(0.5, validation_fraction)))) if len(shuffled) > 1 else 0
    return shuffled[validation_count:], shuffled[:validation_count]


def _class_weights(labels: Any, *, classes: int, torch: Any, device: str) -> Any:
    counts = torch.bincount(labels.cpu(), minlength=classes).float()
    total = float(counts.sum().item()) or 1.0
    weights = total / (max(1, classes) * torch.clamp(counts, min=1.0))
    weights = torch.clamp(weights, min=0.20, max=20.0)
    return weights.to(device)


def _node_class_weights(labels: Any, *, torch: Any, device: str) -> Any:
    weights = _class_weights(labels, classes=len(GRAPH_PARSER_NODE_LABELS), torch=torch, device=device)
    unknown_index = GRAPH_PARSER_NODE_LABELS.index("UNKNOWN")
    weights[unknown_index] = torch.minimum(weights[unknown_index], torch.tensor(1.0, device=device))
    return weights


def _class_weight_report(weights: Any, labels: tuple[str, ...]) -> dict[str, float]:
    values = weights.detach().cpu().tolist()
    return {label: round(float(values[index]), 6) for index, label in enumerate(labels)}


def _evaluate_artifact(artifact: TinyBDGraphParserArtifact, samples: list[dict[str, Any]]) -> dict[str, Any]:
    parser = TinyBDGraphParser(artifact)
    correct = 0
    non_none_correct = 0
    non_none_total = 0
    predicted_non_none = 0
    relation_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    correct_by_label: Counter[str] = Counter()
    for sample in samples:
        probabilities = parser._predict_probabilities(sample["features"])
        predicted = artifact.relation_labels[max(range(len(probabilities)), key=lambda index: probabilities[index])]
        expected = str(sample.get("relation", "NONE"))
        relation_counts.update([expected])
        predicted_counts.update([predicted])
        if predicted == expected:
            correct += 1
            correct_by_label.update([expected])
            if expected != "NONE":
                non_none_correct += 1
        if expected != "NONE":
            non_none_total += 1
        if predicted != "NONE":
            predicted_non_none += 1
    total = len(samples)
    return {
        "accuracy": round(correct / total, 6) if total else 0.0,
        "positive_recall": round(non_none_correct / non_none_total, 6) if non_none_total else 0.0,
        "predicted_positive_rate": round(predicted_non_none / total, 6) if total else 0.0,
        "label_counts": dict(sorted(relation_counts.items())),
        "predicted_counts": dict(sorted(predicted_counts.items())),
        "per_label_recall": _per_label_recall(relation_counts, correct_by_label),
        "per_label_precision": _per_label_precision(predicted_counts, correct_by_label),
    }


def _evaluate_node_artifact(artifact: TinyBDGraphParserArtifact, samples: list[dict[str, Any]]) -> dict[str, Any]:
    parser = TinyBDGraphParser(artifact)
    correct = 0
    label_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    correct_by_label: Counter[str] = Counter()
    for sample in samples:
        probabilities = parser._predict_node_probabilities(sample["features"])
        if not probabilities:
            continue
        predicted = artifact.node_label_names[max(range(len(probabilities)), key=lambda index: probabilities[index])]
        expected = str(sample.get("label", "UNKNOWN"))
        label_counts.update([expected])
        predicted_counts.update([predicted])
        if predicted == expected:
            correct += 1
            correct_by_label.update([expected])
    total = len(samples)
    return {
        "accuracy": round(correct / total, 6) if total else 0.0,
        "label_counts": dict(sorted(label_counts.items())),
        "predicted_counts": dict(sorted(predicted_counts.items())),
        "per_label_recall": _per_label_recall(label_counts, correct_by_label),
        "per_label_precision": _per_label_precision(predicted_counts, correct_by_label),
    }


def _per_label_recall(total_by_label: Counter[str], correct_by_label: Counter[str]) -> dict[str, float]:
    return {
        label: round(correct_by_label.get(label, 0) / count, 6)
        for label, count in sorted(total_by_label.items())
        if count
    }


def _per_label_precision(predicted_by_label: Counter[str], correct_by_label: Counter[str]) -> dict[str, float]:
    return {
        label: round(correct_by_label.get(label, 0) / count, 6)
        for label, count in sorted(predicted_by_label.items())
        if count
    }


if __name__ == "__main__":
    raise SystemExit(main())
