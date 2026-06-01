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
    GRAPH_PARSER_RELATIONS,
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
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
    if not samples:
        raise SystemExit("no graph parser samples")
    train_samples, validation_samples = _split(samples, validation_fraction=args.validation_fraction, seed=args.seed)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    train_x, train_y, means, scales = _tensorize(train_samples, torch=torch)
    val_x, val_y, _means, _scales = _tensorize(validation_samples, torch=torch, means=means, scales=scales)

    layers: list[Any] = []
    width = len(GRAPH_PARSER_FEATURES)
    hidden_width = max(8, int(args.hidden_units))
    for layer_index in range(max(1, int(args.hidden_layers))):
        layers.append(nn.Linear(width if layer_index == 0 else hidden_width, hidden_width))
        layers.append(nn.ReLU())
    layers.append(nn.Linear(hidden_width, len(GRAPH_PARSER_RELATIONS)))
    model = nn.Sequential(*layers).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    class_weights = _class_weights(train_y, classes=len(GRAPH_PARSER_RELATIONS), torch=torch, device=args.device)
    loss_fn = nn.CrossEntropyLoss(weight=class_weights)
    loader = DataLoader(TensorDataset(train_x, train_y), batch_size=max(1, int(args.batch_size)), shuffle=True)
    for _epoch in range(max(1, int(args.epochs))):
        model.train()
        for batch_x, batch_y in loader:
            batch_x = batch_x.to(args.device)
            batch_y = batch_y.to(args.device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()

    artifact = _export_artifact(model, means, scales, args, torch=torch)
    train_eval = _evaluate_artifact(artifact, train_samples[:100000])
    validation_eval = _evaluate_artifact(artifact, validation_samples)
    report = {
        "schema_version": "tinybdmath_graph_parser_train_report_v1",
        "artifact_version": GRAPH_PARSER_ARTIFACT_VERSION,
        "model_version": artifact.model_version,
        "samples": len(samples),
        "train_samples": len(train_samples),
        "validation_samples": len(validation_samples),
        "label_counts": dict(sorted(Counter(str(item["relation"]) for item in samples).items())),
        "train": train_eval,
        "validation": validation_eval,
        "train_config": artifact.train_config,
        "notes": [
            "PyTorch is used only in the isolated training environment.",
            "Exported Graph Parser artifact is candidate-only until verifier/accepted gate passes.",
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


def _export_artifact(model: Any, means: list[float], scales: list[float], args: argparse.Namespace, *, torch: Any) -> TinyBDGraphParserArtifact:
    from torch import nn

    linear_layers = [layer for layer in model if isinstance(layer, nn.Linear)]
    hidden_layers = linear_layers[:-1]
    output = linear_layers[-1]
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
            "seed": int(args.seed),
            "device": str(args.device),
            "torch_version": str(torch.__version__),
        },
    )


def _tensorize(
    samples: list[dict[str, Any]],
    *,
    torch: Any,
    means: list[float] | None = None,
    scales: list[float] | None = None,
) -> tuple[Any, Any, list[float], list[float]]:
    raw = [[float(sample.get("features", {}).get(name, 0.0) or 0.0) for name in GRAPH_PARSER_FEATURES] for sample in samples]
    if means is None:
        means = [
            sum(row[index] for row in raw) / max(1, len(raw))
            for index in range(len(GRAPH_PARSER_FEATURES))
        ]
    if scales is None:
        scales = []
        for index, mean in enumerate(means):
            variance = sum((row[index] - mean) ** 2 for row in raw) / max(1, len(raw))
            scales.append(max(1e-6, variance ** 0.5))
    normalized = [
        [
            (row[index] - means[index]) / (scales[index] if abs(scales[index]) > 1e-12 else 1.0)
            for index in range(len(GRAPH_PARSER_FEATURES))
        ]
        for row in raw
    ]
    relation_to_index = {relation: index for index, relation in enumerate(GRAPH_PARSER_RELATIONS)}
    labels = [relation_to_index.get(str(sample.get("relation", "NONE")), 0) for sample in samples]
    return torch.tensor(normalized, dtype=torch.float32), torch.tensor(labels, dtype=torch.long), means, scales


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


def _evaluate_artifact(artifact: TinyBDGraphParserArtifact, samples: list[dict[str, Any]]) -> dict[str, Any]:
    parser = TinyBDGraphParser(artifact)
    correct = 0
    non_none_correct = 0
    non_none_total = 0
    predicted_non_none = 0
    relation_counts: Counter[str] = Counter()
    for sample in samples:
        probabilities = parser._predict_probabilities(sample["features"])
        predicted = artifact.relation_labels[max(range(len(probabilities)), key=lambda index: probabilities[index])]
        expected = str(sample.get("relation", "NONE"))
        relation_counts.update([expected])
        if predicted == expected:
            correct += 1
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
    }


if __name__ == "__main__":
    raise SystemExit(main())
