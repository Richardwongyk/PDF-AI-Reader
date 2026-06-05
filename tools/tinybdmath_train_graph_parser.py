from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_ARTIFACT_VERSION,
    GRAPH_PARSER_FEATURE_VERSION,
    GRAPH_PARSER_FEATURES,
    GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD,
    GRAPH_PARSER_NODE_FEATURES,
    GRAPH_PARSER_NODE_LABELS,
    GRAPH_PARSER_RELATIONS,
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
    parser.add_argument("--eval-batch-size", type=int, default=65536)
    parser.add_argument("--max-train-eval-samples", type=int, default=100000)
    parser.add_argument("--sample-build-batch-size", type=int, default=512)
    parser.add_argument("--progress-every", type=int, default=5000)
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

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    tensors = _load_training_tensors_streaming(args, torch=torch)
    if int(tensors["samples"]) <= 0:
        raise SystemExit("no graph parser samples")
    if int(tensors["node_samples"]) <= 0:
        raise SystemExit("no graph parser node samples")
    train_x = tensors["train_x"]
    train_y = tensors["train_y"]
    train_weight = tensors["train_weight"]
    val_x = tensors["val_x"]
    val_y = tensors["val_y"]
    train_node_x = tensors["train_node_x"]
    train_node_y = tensors["train_node_y"]
    train_node_weight = tensors["train_node_weight"]
    val_node_x = tensors["val_node_x"]
    val_node_y = tensors["val_node_y"]
    means = tensors["means"]
    scales = tensors["scales"]
    node_means = tensors["node_means"]
    node_scales = tensors["node_scales"]

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
    train_eval_x, train_eval_y = _limit_eval_tensors(
        train_x,
        train_y,
        max_samples=int(args.max_train_eval_samples),
    )
    node_train_eval_x, node_train_eval_y = _limit_eval_tensors(
        train_node_x,
        train_node_y,
        max_samples=int(args.max_train_eval_samples),
    )
    train_eval = _evaluate_torch_model(
        model,
        train_eval_x,
        train_eval_y,
        labels=GRAPH_PARSER_RELATIONS,
        positive_excluded_label="NONE",
        batch_size=int(args.eval_batch_size),
        device=args.device,
        torch=torch,
    )
    validation_eval = _evaluate_torch_model(
        model,
        val_x,
        val_y,
        labels=GRAPH_PARSER_RELATIONS,
        positive_excluded_label="NONE",
        batch_size=int(args.eval_batch_size),
        device=args.device,
        torch=torch,
    )
    node_train_eval = _evaluate_torch_model(
        node_model,
        node_train_eval_x,
        node_train_eval_y,
        labels=GRAPH_PARSER_NODE_LABELS,
        batch_size=int(args.eval_batch_size),
        device=args.device,
        torch=torch,
    )
    node_validation_eval = _evaluate_torch_model(
        node_model,
        val_node_x,
        val_node_y,
        labels=GRAPH_PARSER_NODE_LABELS,
        batch_size=int(args.eval_batch_size),
        device=args.device,
        torch=torch,
    )
    report = {
        "schema_version": "tinybdmath_graph_parser_train_report_v1",
        "artifact_version": GRAPH_PARSER_ARTIFACT_VERSION,
        "model_version": artifact.model_version,
        "samples": int(tensors["samples"]),
        "node_samples": int(tensors["node_samples"]),
        "train_samples": int(train_y.numel()),
        "validation_samples": int(val_y.numel()),
        "train_node_samples": int(train_node_y.numel()),
        "validation_node_samples": int(val_node_y.numel()),
        "label_counts": _label_count_report(tensors["all_y_counts"], GRAPH_PARSER_RELATIONS),
        "node_label_counts": _label_count_report(tensors["all_node_y_counts"], GRAPH_PARSER_NODE_LABELS),
        "weighted_label_counts": _weighted_label_count_report(tensors["all_y_weighted"], GRAPH_PARSER_RELATIONS),
        "node_weighted_label_counts": _weighted_label_count_report(tensors["all_node_y_weighted"], GRAPH_PARSER_NODE_LABELS),
        "weight_summary": tensors["weight_summary"],
        "node_weight_summary": tensors["node_weight_summary"],
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
            "eval_batch_size": int(args.eval_batch_size),
            "max_train_eval_samples": int(args.max_train_eval_samples),
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


def _load_training_tensors_streaming(args: argparse.Namespace, *, torch: Any) -> dict[str, Any]:
    train_chunks: list[Any] = []
    train_y_chunks: list[Any] = []
    train_weight_chunks: list[Any] = []
    val_chunks: list[Any] = []
    val_y_chunks: list[Any] = []
    node_train_chunks: list[Any] = []
    node_train_y_chunks: list[Any] = []
    node_train_weight_chunks: list[Any] = []
    node_val_chunks: list[Any] = []
    node_val_y_chunks: list[Any] = []
    all_y_counts = torch.zeros(len(GRAPH_PARSER_RELATIONS), dtype=torch.long)
    all_node_y_counts = torch.zeros(len(GRAPH_PARSER_NODE_LABELS), dtype=torch.long)
    all_y_weighted = torch.zeros(len(GRAPH_PARSER_RELATIONS), dtype=torch.float64)
    all_node_y_weighted = torch.zeros(len(GRAPH_PARSER_NODE_LABELS), dtype=torch.float64)
    weight_stats = _new_weight_stats()
    node_weight_stats = _new_weight_stats()
    total_rows = 0
    total_samples = 0
    total_node_samples = 0
    for graph_chunk, alignment_chunk in _iter_training_input_chunks(
        args.graph_rows,
        args.alignment_rows,
        batch_size=int(args.sample_build_batch_size),
        limit=int(args.limit),
    ):
        total_rows += len(graph_chunk)
        samples = training_samples_from_rows(graph_chunk, alignment_chunk)
        node_samples = node_training_samples_from_rows(graph_chunk, alignment_chunk)
        total_samples += len(samples)
        total_node_samples += len(node_samples)
        _update_global_counts(
            samples,
            labels=GRAPH_PARSER_RELATIONS,
            label_key="relation",
            count_tensor=all_y_counts,
            weighted_tensor=all_y_weighted,
            weight_stats=weight_stats,
            torch=torch,
        )
        _update_global_counts(
            node_samples,
            labels=GRAPH_PARSER_NODE_LABELS,
            label_key="label",
            count_tensor=all_node_y_counts,
            weighted_tensor=all_node_y_weighted,
            weight_stats=node_weight_stats,
            torch=torch,
        )
        sample_train, sample_val = _split_streaming_samples(
            samples,
            validation_fraction=float(args.validation_fraction),
            seed=int(args.seed),
            salt="relation",
        )
        node_train, node_val = _split_streaming_samples(
            node_samples,
            validation_fraction=float(args.validation_fraction),
            seed=int(args.seed),
            salt="node",
        )
        _append_tensor_chunk(
            sample_train,
            feature_names=GRAPH_PARSER_FEATURES,
            labels=GRAPH_PARSER_RELATIONS,
            label_key="relation",
            x_chunks=train_chunks,
            y_chunks=train_y_chunks,
            weight_chunks=train_weight_chunks,
            torch=torch,
        )
        _append_tensor_chunk(
            sample_val,
            feature_names=GRAPH_PARSER_FEATURES,
            labels=GRAPH_PARSER_RELATIONS,
            label_key="relation",
            x_chunks=val_chunks,
            y_chunks=val_y_chunks,
            weight_chunks=None,
            torch=torch,
        )
        _append_tensor_chunk(
            node_train,
            feature_names=GRAPH_PARSER_NODE_FEATURES,
            labels=GRAPH_PARSER_NODE_LABELS,
            label_key="label",
            x_chunks=node_train_chunks,
            y_chunks=node_train_y_chunks,
            weight_chunks=node_train_weight_chunks,
            torch=torch,
        )
        _append_tensor_chunk(
            node_val,
            feature_names=GRAPH_PARSER_NODE_FEATURES,
            labels=GRAPH_PARSER_NODE_LABELS,
            label_key="label",
            x_chunks=node_val_chunks,
            y_chunks=node_val_y_chunks,
            weight_chunks=None,
            torch=torch,
        )
        if int(args.progress_every) > 0 and total_rows % int(args.progress_every) < len(graph_chunk):
            print(
                json.dumps(
                    {
                        "event": "training_sample_progress",
                        "rows": total_rows,
                        "samples": total_samples,
                        "node_samples": total_node_samples,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    train_raw = _cat_or_empty(train_chunks, width=len(GRAPH_PARSER_FEATURES), torch=torch)
    val_raw = _cat_or_empty(val_chunks, width=len(GRAPH_PARSER_FEATURES), torch=torch)
    train_y = _cat_labels_or_empty(train_y_chunks, torch=torch)
    val_y = _cat_labels_or_empty(val_y_chunks, torch=torch)
    train_weight = _cat_weights_or_empty(train_weight_chunks, torch=torch)
    train_node_raw = _cat_or_empty(node_train_chunks, width=len(GRAPH_PARSER_NODE_FEATURES), torch=torch)
    val_node_raw = _cat_or_empty(node_val_chunks, width=len(GRAPH_PARSER_NODE_FEATURES), torch=torch)
    train_node_y = _cat_labels_or_empty(node_train_y_chunks, torch=torch)
    val_node_y = _cat_labels_or_empty(node_val_y_chunks, torch=torch)
    train_node_weight = _cat_weights_or_empty(node_train_weight_chunks, torch=torch)
    train_x, means, scales = _normalize_raw_tensor(train_raw, torch=torch)
    val_x, _unused_means, _unused_scales = _normalize_raw_tensor(val_raw, means=means, scales=scales, torch=torch)
    train_node_x, node_means, node_scales = _normalize_raw_tensor(train_node_raw, torch=torch)
    val_node_x, _unused_node_means, _unused_node_scales = _normalize_raw_tensor(
        val_node_raw,
        means=node_means,
        scales=node_scales,
        torch=torch,
    )
    return {
        "samples": total_samples,
        "node_samples": total_node_samples,
        "train_x": train_x,
        "train_y": train_y,
        "train_weight": train_weight,
        "val_x": val_x,
        "val_y": val_y,
        "train_node_x": train_node_x,
        "train_node_y": train_node_y,
        "train_node_weight": train_node_weight,
        "val_node_x": val_node_x,
        "val_node_y": val_node_y,
        "means": means,
        "scales": scales,
        "node_means": node_means,
        "node_scales": node_scales,
        "all_y_counts": all_y_counts,
        "all_node_y_counts": all_node_y_counts,
        "all_y_weighted": all_y_weighted,
        "all_node_y_weighted": all_node_y_weighted,
        "weight_summary": _finish_weight_stats(weight_stats),
        "node_weight_summary": _finish_weight_stats(node_weight_stats),
    }


def _iter_training_input_chunks(
    graph_rows_path: Path,
    alignment_rows_path: Path,
    *,
    batch_size: int,
    limit: int = 0,
) -> Any:
    chunk_size = max(1, int(batch_size or 1))
    row_limit = int(limit or 0)
    graph_chunk: list[dict[str, Any]] = []
    alignment_chunk: list[dict[str, Any]] = []
    total = 0
    with graph_rows_path.open("r", encoding="utf-8") as graph_handle, alignment_rows_path.open("r", encoding="utf-8") as alignment_handle:
        while row_limit <= 0 or total < row_limit:
            graph_row = _read_next_json_object(graph_handle)
            alignment_row = _read_next_json_object(alignment_handle)
            if graph_row is None or alignment_row is None:
                break
            graph_chunk.append(graph_row)
            alignment_chunk.append(alignment_row)
            total += 1
            if len(graph_chunk) >= chunk_size:
                yield graph_chunk, alignment_chunk
                graph_chunk = []
                alignment_chunk = []
    if graph_chunk:
        yield graph_chunk, alignment_chunk


def _read_next_json_object(handle: Any) -> dict[str, Any] | None:
    for line in handle:
        text = line.strip()
        if not text:
            continue
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    return None


def _split_streaming_samples(
    samples: list[dict[str, Any]],
    *,
    validation_fraction: float,
    seed: int,
    salt: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fraction = max(0.0, min(0.5, float(validation_fraction)))
    if fraction <= 0.0:
        return samples, []
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for sample in samples:
        if _sample_split_score(sample, seed=seed, salt=salt) < fraction:
            validation.append(sample)
        else:
            train.append(sample)
    return train, validation


def _sample_split_score(sample: dict[str, Any], *, seed: int, salt: str) -> float:
    payload = {
        "seed": int(seed),
        "salt": salt,
        "row_id": sample.get("row_id", ""),
        "source": sample.get("source", sample.get("node_id", "")),
        "target": sample.get("target", ""),
        "label": sample.get("relation", sample.get("label", "")),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _append_tensor_chunk(
    samples: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    labels: tuple[str, ...],
    label_key: str,
    x_chunks: list[Any],
    y_chunks: list[Any],
    weight_chunks: list[Any] | None,
    torch: Any,
) -> None:
    if not samples:
        return
    x, y, weights = _samples_to_raw_tensors(
        samples,
        feature_names=feature_names,
        labels=labels,
        label_key=label_key,
        torch=torch,
    )
    x_chunks.append(x)
    y_chunks.append(y)
    if weight_chunks is not None:
        weight_chunks.append(weights)


def _samples_to_raw_tensors(
    samples: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    labels: tuple[str, ...],
    label_key: str,
    torch: Any,
) -> tuple[Any, Any, Any]:
    raw = torch.empty((len(samples), len(feature_names)), dtype=torch.float32)
    label_to_index = {label: index for index, label in enumerate(labels)}
    label_values: list[int] = []
    weights: list[float] = []
    for row_index, sample in enumerate(samples):
        features = sample.get("features", {})
        if not isinstance(features, dict):
            features = {}
        for column_index, name in enumerate(feature_names):
            raw[row_index, column_index] = float(features.get(name, 0.0) or 0.0)
        label_values.append(label_to_index.get(str(sample.get(label_key, labels[0])), 0))
        weights.append(_sample_weight(sample))
    return (
        raw,
        torch.tensor(label_values, dtype=torch.long),
        torch.tensor(weights, dtype=torch.float32),
    )


def _cat_or_empty(chunks: list[Any], *, width: int, torch: Any) -> Any:
    if not chunks:
        return torch.empty((0, width), dtype=torch.float32)
    if len(chunks) == 1:
        return chunks[0]
    return torch.cat(chunks, dim=0)


def _cat_labels_or_empty(chunks: list[Any], *, torch: Any) -> Any:
    if not chunks:
        return torch.empty((0,), dtype=torch.long)
    if len(chunks) == 1:
        return chunks[0]
    return torch.cat(chunks, dim=0)


def _cat_weights_or_empty(chunks: list[Any], *, torch: Any) -> Any:
    if not chunks:
        return torch.empty((0,), dtype=torch.float32)
    if len(chunks) == 1:
        return chunks[0]
    return torch.cat(chunks, dim=0)


def _normalize_raw_tensor(
    raw: Any,
    *,
    torch: Any,
    means: list[float] | None = None,
    scales: list[float] | None = None,
) -> tuple[Any, list[float], list[float]]:
    if means is None:
        means_tensor = raw.mean(dim=0) if raw.shape[0] else torch.zeros(raw.shape[1], dtype=torch.float32)
        means = [float(value) for value in means_tensor.tolist()]
    else:
        means_tensor = torch.tensor(means, dtype=torch.float32)
    if scales is None:
        scales_tensor = raw.std(dim=0, unbiased=False).clamp_min(1e-6) if raw.shape[0] else torch.ones(raw.shape[1], dtype=torch.float32)
        scales = [float(value) for value in scales_tensor.tolist()]
    else:
        scales_tensor = torch.tensor(scales, dtype=torch.float32).clamp_min(1e-6)
    if raw.shape[0]:
        raw.sub_(means_tensor[: raw.shape[1]]).div_(scales_tensor[: raw.shape[1]].clamp_min(1e-6))
    return raw, means, scales


def _update_global_counts(
    samples: list[dict[str, Any]],
    *,
    labels: tuple[str, ...],
    label_key: str,
    count_tensor: Any,
    weighted_tensor: Any,
    weight_stats: dict[str, float],
    torch: Any,
) -> None:
    if not samples:
        return
    label_to_index = {label: index for index, label in enumerate(labels)}
    label_values = []
    weights = []
    for sample in samples:
        label_values.append(label_to_index.get(str(sample.get(label_key, labels[0])), 0))
        weights.append(_sample_weight(sample))
    y = torch.tensor(label_values, dtype=torch.long)
    count_tensor += torch.bincount(y, minlength=len(labels)).long()
    for label_index, weight in zip(label_values, weights):
        weighted_tensor[label_index] += float(weight)
    _update_weight_stats(weight_stats, weights)


def _new_weight_stats() -> dict[str, float]:
    return {"count": 0.0, "sum": 0.0, "min": float("inf"), "max": 0.0}


def _update_weight_stats(stats: dict[str, float], weights: list[float]) -> None:
    if not weights:
        return
    stats["count"] += float(len(weights))
    stats["sum"] += float(sum(weights))
    stats["min"] = min(float(stats["min"]), float(min(weights)))
    stats["max"] = max(float(stats["max"]), float(max(weights)))


def _finish_weight_stats(stats: dict[str, float]) -> dict[str, float]:
    count = int(stats.get("count", 0.0) or 0.0)
    if count <= 0:
        return {"min": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "min": round(float(stats["min"]), 6),
        "max": round(float(stats["max"]), 6),
        "mean": round(float(stats["sum"]) / count, 6),
    }


def _weighted_label_count_report(values: Any, labels: tuple[str, ...]) -> dict[str, float]:
    items = values.detach().cpu().tolist()
    return {
        label: round(float(items[index]), 6)
        for index, label in enumerate(labels)
        if abs(float(items[index])) > 1e-12
    }


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
    raw = torch.empty((len(samples), len(feature_names)), dtype=torch.float32)
    for row_index, sample in enumerate(samples):
        features = sample.get("features", {})
        if not isinstance(features, dict):
            features = {}
        for column_index, name in enumerate(feature_names):
            raw[row_index, column_index] = float(features.get(name, 0.0) or 0.0)
    if means is None:
        means_tensor = raw.mean(dim=0) if len(samples) else torch.zeros(len(feature_names), dtype=torch.float32)
        means = [float(value) for value in means_tensor.tolist()]
    else:
        means_tensor = torch.tensor(means, dtype=torch.float32)
    if scales is None:
        scales_tensor = raw.std(dim=0, unbiased=False).clamp_min(1e-6) if len(samples) else torch.ones(len(feature_names), dtype=torch.float32)
        scales = [float(value) for value in scales_tensor.tolist()]
    else:
        scales_tensor = torch.tensor(scales, dtype=torch.float32).clamp_min(1e-6)
    if len(means_tensor) < len(feature_names):
        means_tensor = torch.cat([means_tensor, torch.zeros(len(feature_names) - len(means_tensor), dtype=torch.float32)])
    if len(scales_tensor) < len(feature_names):
        scales_tensor = torch.cat([scales_tensor, torch.ones(len(feature_names) - len(scales_tensor), dtype=torch.float32)])
    normalized = (raw - means_tensor[: len(feature_names)]) / scales_tensor[: len(feature_names)].clamp_min(1e-6)
    label_to_index = {label: index for index, label in enumerate(labels)}
    label_values = [label_to_index.get(str(sample.get(label_key, labels[0])), 0) for sample in samples]
    weights = [_sample_weight(sample) for sample in samples]
    return (
        normalized,
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


def _limit_eval_tensors(x: Any, y: Any, *, max_samples: int) -> tuple[Any, Any]:
    if max_samples <= 0 or x.shape[0] <= max_samples:
        return x, y
    return x[:max_samples], y[:max_samples]


def _evaluate_torch_model(
    model: Any,
    x: Any,
    y: Any,
    *,
    labels: tuple[str, ...],
    batch_size: int,
    device: str,
    torch: Any,
    positive_excluded_label: str | None = None,
) -> dict[str, Any]:
    model.eval()
    class_count = len(labels)
    label_counts = torch.bincount(y.cpu(), minlength=class_count).long()
    predicted_counts = torch.zeros(class_count, dtype=torch.long)
    correct_by_label = torch.zeros(class_count, dtype=torch.long)
    correct = 0
    total = int(y.numel())
    with torch.no_grad():
        for start in range(0, total, max(1, int(batch_size))):
            end = min(total, start + max(1, int(batch_size)))
            batch_x = x[start:end].to(device)
            expected = y[start:end].cpu()
            predicted = model(batch_x).argmax(dim=1).cpu()
            predicted_counts += torch.bincount(predicted, minlength=class_count).long()
            matched = predicted == expected
            correct += int(matched.sum().item())
            if bool(matched.any()):
                correct_by_label += torch.bincount(expected[matched], minlength=class_count).long()
    report: dict[str, Any] = {
        "accuracy": round(correct / total, 6) if total else 0.0,
        "label_counts": _label_count_report(label_counts, labels),
        "predicted_counts": _label_count_report(predicted_counts, labels),
        "per_label_recall": _tensor_per_label_recall(label_counts, correct_by_label, labels),
        "per_label_precision": _tensor_per_label_precision(predicted_counts, correct_by_label, labels),
    }
    if positive_excluded_label is not None and positive_excluded_label in labels:
        excluded_index = labels.index(positive_excluded_label)
        non_excluded_total = int(total - int(label_counts[excluded_index].item()))
        non_excluded_correct = int(correct - int(correct_by_label[excluded_index].item()))
        predicted_non_excluded = int(total - int(predicted_counts[excluded_index].item()))
        report["positive_recall"] = round(non_excluded_correct / non_excluded_total, 6) if non_excluded_total else 0.0
        report["predicted_positive_rate"] = round(predicted_non_excluded / total, 6) if total else 0.0
    return report


def _label_count_report(counts: Any, labels: tuple[str, ...]) -> dict[str, int]:
    values = counts.detach().cpu().tolist()
    return {label: int(values[index]) for index, label in enumerate(labels) if int(values[index])}


def _tensor_per_label_recall(total_by_label: Any, correct_by_label: Any, labels: tuple[str, ...]) -> dict[str, float]:
    totals = total_by_label.detach().cpu().tolist()
    correct = correct_by_label.detach().cpu().tolist()
    return {
        label: round(float(correct[index]) / float(count), 6)
        for index, (label, count) in enumerate(zip(labels, totals))
        if int(count)
    }


def _tensor_per_label_precision(predicted_by_label: Any, correct_by_label: Any, labels: tuple[str, ...]) -> dict[str, float]:
    predicted = predicted_by_label.detach().cpu().tolist()
    correct = correct_by_label.detach().cpu().tolist()
    return {
        label: round(float(correct[index]) / float(count), 6)
        for index, (label, count) in enumerate(zip(labels, predicted))
        if int(count)
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
