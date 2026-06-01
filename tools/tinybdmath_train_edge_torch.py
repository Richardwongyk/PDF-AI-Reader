"""Train TinyBDMath edge relation model with PyTorch.

Run this from the isolated ``science`` environment.  It exports the lightweight
JSON edge model consumed by the main app, so the app environment does not need
PyTorch at runtime.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_edge_baseline import (  # noqa: E402
    EDGE_BASELINE_VERSION,
    EDGE_FEATURES,
    EDGE_FEATURES_V2,
    EDGE_LABELS,
    EDGE_MLP_VERSION,
    TinyBDEdgeBaselineModel,
    add_graph_context_features,
    edge_features,
    evaluate_edge_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--relation-labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8192)
    parser.add_argument("--architecture", choices=("mlp", "linear"), default="mlp")
    parser.add_argument("--hidden-units", type=int, default=128)
    parser.add_argument("--hidden-layers", type=int, default=2)
    parser.add_argument("--feature-set", choices=("edge", "graph_context"), default="graph_context")
    parser.add_argument("--learning-rate", type=float, default=0.02)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--validation-fraction", type=float, default=0.10)
    parser.add_argument("--calibrate-logit-scale", action="store_true")
    parser.add_argument(
        "--calibration-objective",
        choices=("candidate_relation_f1", "accepted_precision"),
        default="candidate_relation_f1",
    )
    parser.add_argument("--candidate-threshold", type=float, default=0.70)
    parser.add_argument("--no-class-weights", action="store_true")
    parser.add_argument("--seed", type=int, default=20260526)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()

    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "torch_unavailable", "detail": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    samples = _samples_joined(args.graph_rows, args.relation_labels, limit_rows=args.limit)
    if not samples:
        raise SystemExit("no edge samples")
    torch.manual_seed(args.seed)
    train_samples, validation_samples = _split(samples, validation_fraction=args.validation_fraction, seed=args.seed)
    feature_names = EDGE_FEATURES_V2 if args.feature_set == "graph_context" else EDGE_FEATURES
    train_x, train_y, means, scales = _tensorize(train_samples, torch=torch, feature_names=feature_names)
    val_x, val_y, _means, _scales = _tensorize(validation_samples, torch=torch, means=means, scales=scales, feature_names=feature_names)

    if args.architecture == "mlp":
        layers: list[Any] = []
        input_width = len(feature_names)
        hidden_width = max(4, int(args.hidden_units))
        for layer_index in range(max(1, int(args.hidden_layers))):
            layers.append(nn.Linear(input_width if layer_index == 0 else hidden_width, hidden_width))
            layers.append(nn.ReLU())
        layers.append(nn.Linear(hidden_width, len(EDGE_LABELS)))
        model = nn.Sequential(*layers).to(args.device)
    else:
        model = nn.Linear(len(feature_names), len(EDGE_LABELS)).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    class_weights = (
        None
        if args.no_class_weights
        else _class_weights(train_y, classes=len(EDGE_LABELS), torch=torch, device=args.device)
    )
    loss_fn = nn.CrossEntropyLoss(weight=class_weights)
    loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=max(1, int(args.batch_size)),
        shuffle=True,
    )
    for _epoch in range(max(1, int(args.epochs))):
        model.train()
        for batch_x, batch_y in loader:
            batch_x = batch_x.to(args.device)
            batch_y = batch_y.to(args.device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()

    scale_factor, calibration_report = _calibrate_logit_scale(
        model,
        val_x,
        val_y,
        torch=torch,
        device=args.device,
        objective=args.calibration_objective,
        candidate_threshold=args.candidate_threshold,
    ) if args.calibrate_logit_scale else (1.0, {"enabled": False})
    exported_means = list(means)
    exported_scales = list(scales)
    bias_index = list(EDGE_FEATURES).index("bias")
    if args.architecture == "mlp":
        linear_layers = [layer for layer in model if isinstance(layer, nn.Linear)]
        hidden_layers = linear_layers[:-1]
        output = linear_layers[-1]
        exported_hidden_weights = [hidden.weight.detach().cpu().tolist() for hidden in hidden_layers]
        exported_hidden_biases = [
            hidden.bias.detach().cpu().tolist() if hidden.bias is not None else []
            for hidden in hidden_layers
        ]
        exported_output_weights = [[float(value) * scale_factor for value in row] for row in output.weight.detach().cpu().tolist()]
        exported_output_bias = [
            float(value) * scale_factor
            for value in (output.bias.detach().cpu().tolist() if output.bias is not None else [0.0 for _ in EDGE_LABELS])
        ]
        exported_weights: list[list[float]] = []
        model_version = EDGE_MLP_VERSION
        model_type = "mlp_relu"
    else:
        exported_means[bias_index] = 0.0
        exported_scales[bias_index] = 1.0
        exported_weights = [[float(value) * scale_factor for value in row] for row in model.weight.detach().cpu().tolist()]
        exported_bias = model.bias.detach().cpu().tolist() if model.bias is not None else [0.0 for _ in EDGE_LABELS]
        for class_index, bias_value in enumerate(exported_bias):
            exported_weights[class_index][bias_index] = float(bias_value) * scale_factor
        exported_hidden_weights = []
        exported_hidden_biases = []
        exported_output_weights = []
        exported_output_bias = []
        model_version = EDGE_BASELINE_VERSION
        model_type = "linear_softmax"
    exported = TinyBDEdgeBaselineModel(
        version=model_version,
        feature_names=tuple(feature_names),
        labels=EDGE_LABELS,
        weights=tuple(tuple(round(float(v), 8) for v in row) for row in exported_weights),
        means=tuple(round(float(v), 8) for v in exported_means),
        scales=tuple(round(float(v), 8) for v in exported_scales),
        train_config={
            "mode": "torch_edge_relation",
            "architecture": str(args.architecture),
            "feature_set": str(args.feature_set),
            "hidden_units": int(max(4, int(args.hidden_units))) if args.architecture == "mlp" else 0,
            "hidden_layers": int(max(1, int(args.hidden_layers))) if args.architecture == "mlp" else 0,
            "class_weighted_loss": not bool(args.no_class_weights),
            "class_weights": _tensor_to_list(class_weights) if class_weights is not None else [],
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "learning_rate": float(args.learning_rate),
            "weight_decay": float(args.weight_decay),
            "seed": int(args.seed),
            "device": str(args.device),
            "torch_version": str(torch.__version__),
            "logit_scale_factor": float(scale_factor),
            "calibration_objective": str(args.calibration_objective) if args.calibrate_logit_scale else "",
            "candidate_threshold": float(args.candidate_threshold),
        },
        model_type=model_type,
        hidden_weights=tuple(
            tuple(tuple(round(float(v), 8) for v in row) for row in layer)
            for layer in exported_hidden_weights
        ),
        hidden_biases=tuple(tuple(round(float(v), 8) for v in layer) for layer in exported_hidden_biases),
        output_weights=tuple(tuple(round(float(v), 8) for v in row) for row in exported_output_weights),
        output_bias=tuple(round(float(v), 8) for v in exported_output_bias),
    )
    report = {
        "schema_version": "tinybdmath_edge_torch_report_v1",
        "model_version": model_version,
        "architecture": str(args.architecture),
        "samples": len(samples),
        "train_samples": len(train_samples),
        "validation_samples": len(validation_samples),
        "train": evaluate_edge_baseline(exported, train_samples[:100000]),
        "validation": evaluate_edge_baseline(exported, validation_samples),
        "calibration": calibration_report,
        "label_counts": _label_counts(samples),
        "notes": [
            "PyTorch is used only in the isolated training environment.",
            "Exported JSON model is candidate-only evidence for r2a/fusion/verifier.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    exported.save(args.output_dir / "tinybdmath_edge_baseline_model.json")
    (args.output_dir / "tinybdmath_edge_torch_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _class_weights(labels: Any, *, classes: int, torch: Any, device: str) -> Any:
    counts = torch.bincount(labels.cpu(), minlength=classes).float()
    total = float(counts.sum().item()) or 1.0
    weights = total / (max(1, classes) * torch.clamp(counts, min=1.0))
    weights = torch.clamp(weights, min=0.25, max=12.0)
    return weights.to(device)


def _tensor_to_list(value: Any) -> list[float]:
    return [round(float(item), 8) for item in value.detach().cpu().tolist()]


def _calibrate_logit_scale(
    model: Any,
    features: Any,
    labels: Any,
    *,
    torch: Any,
    device: str,
    objective: str,
    candidate_threshold: float,
) -> tuple[float, dict[str, Any]]:
    candidates = [0.50, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]
    metrics: list[dict[str, Any]] = []
    model.eval()
    with torch.no_grad():
        logits = model(features.to(device)).cpu()
        labels_cpu = labels.cpu()
    for scale in candidates:
        probs = torch.softmax(logits * float(scale), dim=1)
        confidence, predictions = torch.max(probs, dim=1)
        correct = predictions == labels_cpu
        high_055 = confidence >= 0.55
        high_070 = confidence >= 0.70
        metrics.append(
            {
                "scale": float(scale),
                "accuracy": round(float(correct.float().mean().item()), 6),
                "coverage_055": round(float(high_055.float().mean().item()), 6),
                "precision_055": _precision_at_mask(correct, high_055, torch=torch),
                "coverage_070": round(float(high_070.float().mean().item()), 6),
                "precision_070": _precision_at_mask(correct, high_070, torch=torch),
                "candidate_relation": _candidate_relation_metrics(
                    predictions,
                    confidence,
                    labels_cpu,
                    threshold=float(candidate_threshold),
                    torch=torch,
                ),
            }
        )
    if objective == "accepted_precision":
        best = max(metrics, key=lambda item: (item["precision_070"] >= 0.995, item["coverage_070"], item["precision_070"]))
    else:
        best = max(
            metrics,
            key=lambda item: (
                item["candidate_relation"]["f1"],
                item["candidate_relation"]["recall"],
                item["candidate_relation"]["precision"],
            ),
        )
    return float(best["scale"]), {
        "enabled": True,
        "objective": str(objective),
        "candidate_threshold": float(candidate_threshold),
        "selected": best,
        "candidates": metrics,
    }


def _candidate_relation_metrics(predictions: Any, confidence: Any, labels: Any, *, threshold: float, torch: Any) -> dict[str, Any]:
    no_relation = EDGE_LABELS.index("NO_RELATION")
    predicted_relation = (predictions != no_relation) & (confidence >= float(threshold))
    expected_relation = labels != no_relation
    correct_relation = predicted_relation & expected_relation & (predictions == labels)
    tp = int(correct_relation.sum().item())
    fp = int((predicted_relation & ~correct_relation).sum().item())
    fn = int((expected_relation & ~correct_relation).sum().item())
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": float(threshold),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(float(precision), 6),
        "recall": round(float(recall), 6),
        "f1": round(float(f1), 6),
        "predicted_relation_rate": round(float(predicted_relation.float().mean().item()), 6),
    }


def _precision_at_mask(correct: Any, mask: Any, *, torch: Any) -> float:
    count = int(mask.sum().item())
    if count <= 0:
        return 1.0
    return round(float(correct[mask].float().mean().item()), 6)


def _samples_joined(graph_rows: Path, relation_labels: Path, *, limit_rows: int = 0) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    rows_seen = 0
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
            edges_with_context = {
                str(edge.get("edge_id", "")): edge
                for edge in add_graph_context_features(list(edge_by_id.values()))
            }
            for label in label_row.get("edge_labels", []):
                if not isinstance(label, dict):
                    continue
                relation = str(label.get("label", ""))
                if relation not in EDGE_LABELS:
                    continue
                edge_id = str(label.get("edge_id", ""))
                edge = dict(edges_with_context.get(edge_id, {}))
                if not edge:
                    continue
                edge["row_id"] = row_id
                edge["edge_id"] = edge_id
                edge["label"] = relation
                samples.append(edge)
            rows_seen += 1
            if limit_rows > 0 and rows_seen >= limit_rows:
                break
    return samples


def _tensorize(
    samples: list[dict[str, Any]],
    *,
    torch: Any,
    feature_names: tuple[str, ...],
    means: list[float] | None = None,
    scales: list[float] | None = None,
) -> tuple[Any, Any, list[float], list[float]]:
    vectors = [[float(edge_features(sample).get(name, 0.0) or 0.0) for name in feature_names] for sample in samples]
    if means is None or scales is None:
        means, scales = _stats(vectors, len(feature_names))
    normalized = [[(value - means[index]) / max(scales[index], 1e-6) for index, value in enumerate(vector)] for vector in vectors]
    label_to_index = {label: index for index, label in enumerate(EDGE_LABELS)}
    labels = [label_to_index[str(sample.get("label", "NO_RELATION"))] for sample in samples]
    return torch.tensor(normalized, dtype=torch.float32), torch.tensor(labels, dtype=torch.long), means, scales


def _stats(vectors: list[list[float]], width: int) -> tuple[list[float], list[float]]:
    if not vectors:
        return [0.0] * width, [1.0] * width
    means: list[float] = []
    scales: list[float] = []
    for index in range(width):
        values = [vector[index] for vector in vectors]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean)
        scales.append(max(math.sqrt(variance), 1e-6))
    return means, scales


def _split(samples: list[dict[str, Any]], *, validation_fraction: float, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import hashlib

    modulo = max(2, int(round(1.0 / max(0.01, min(0.5, validation_fraction)))))
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for sample in samples:
        key = f"{seed}:{sample.get('row_id','')}:{sample.get('edge_id','')}"
        bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % modulo
        (validation if bucket == 0 else train).append(sample)
    if not validation and train:
        validation.append(train.pop())
    return train, validation


def _label_counts(samples: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(sample.get("label", "")) for sample in samples)
    return {label: int(counts.get(label, 0)) for label in EDGE_LABELS}


if __name__ == "__main__":
    raise SystemExit(main())
