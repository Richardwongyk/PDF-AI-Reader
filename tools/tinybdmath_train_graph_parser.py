from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_ARTIFACT_VERSION,
    GRAPH_PARSER_FEATURE_VERSION,
    GRAPH_PARSER_FEATURES,
    GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD,
    GRAPH_PARSER_RUNTIME_RELATION_CONFIDENCE_FLOOR,
    GRAPH_PARSER_NODE_FEATURES,
    GRAPH_PARSER_NODE_LABELS,
    GRAPH_PARSER_RELATIONS,
    TinyBDGraphParserArtifact,
    node_training_samples_from_rows,
    training_samples_from_rows,
)

GRAPH_PARSER_RELATION_FAMILIES = (
    "NONE",
    "LINEAR",
    "ATTACH",
    "RADIAL",
    "FENCE",
    "MATRIX",
    "TAG",
)

GRAPH_PARSER_RELATION_TO_FAMILY = {
    "NONE": "NONE",
    "NEXT": "LINEAR",
    "TEXT_RUN_NEXT": "LINEAR",
    "SUB": "ATTACH",
    "SUP": "ATTACH",
    "PRE_SUB": "ATTACH",
    "PRE_SUP": "ATTACH",
    "UNDER": "ATTACH",
    "OVER": "ATTACH",
    "ACCENT_BASE": "ATTACH",
    "NUMERATOR": "RADIAL",
    "DENOMINATOR": "RADIAL",
    "RADICAL_BODY": "RADIAL",
    "RADICAL_INDEX": "RADIAL",
    "FRACTION_BAR": "RADIAL",
    "OVERLINE": "RADIAL",
    "UNDERLINE": "RADIAL",
    "ABOVE": "RADIAL",
    "BELOW": "RADIAL",
    "FENCE_BODY": "FENCE",
    "FENCE_OPEN": "FENCE",
    "FENCE_CLOSE": "FENCE",
    "ENCLOSURE_BODY": "FENCE",
    "MATRIX_ROW": "MATRIX",
    "MATRIX_CELL": "MATRIX",
    "CELL_CONTENT": "MATRIX",
    "EQUATION_TAG": "TAG",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Train TinyBDMath Graph Parser M4 with PyTorch.")
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
    parser.add_argument("--threshold", type=float, default=GRAPH_PARSER_RUNTIME_RELATION_CONFIDENCE_FLOOR)
    parser.add_argument("--node-filter-threshold", type=float, default=GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD)
    parser.add_argument("--eval-batch-size", type=int, default=65536)
    parser.add_argument("--max-train-eval-samples", type=int, default=100000)
    parser.add_argument("--sample-build-batch-size", type=int, default=512)
    parser.add_argument("--progress-every", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260601)
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--relation-architecture",
        choices=("graph_parser_m2", "graph_parser_m3", "graph_parser_m4"),
        default="graph_parser_m4",
    )
    parser.add_argument("--keep-threshold", type=float, default=0.5)
    parser.add_argument("--keep-loss-weight", type=float, default=0.5)
    parser.add_argument("--tensor-cache-dir", type=Path, default=Path(".tensor_cache") / "tinybdmath_train_graph_parser")
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
    tensors = _load_training_tensors_cached(args, torch=torch)
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

    node_model = _build_node_context_model(
        input_width=len(GRAPH_PARSER_NODE_FEATURES),
        output_width=len(GRAPH_PARSER_NODE_LABELS),
        hidden_units=max(8, int(args.hidden_units) // 2),
        hidden_layers=max(1, int(args.hidden_layers)),
        nn=nn,
    ).to(args.device)
    model = _build_context_relation_model(
        input_width=len(GRAPH_PARSER_FEATURES),
        output_width=len(GRAPH_PARSER_RELATIONS),
        node_model=node_model,
        node_output_width=len(GRAPH_PARSER_NODE_LABELS),
        hidden_units=int(args.hidden_units),
        hidden_layers=int(args.hidden_layers),
        nn=nn,
        torch=torch,
        architecture_mode=str(args.relation_architecture),
    ).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    node_optimizer = torch.optim.AdamW(node_model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    class_weights = _class_weights(train_y, classes=len(GRAPH_PARSER_RELATIONS), torch=torch, device=args.device)
    node_class_weights = _node_class_weights(train_node_y, torch=torch, device=args.device)
    loss_fn = nn.CrossEntropyLoss(weight=class_weights, reduction="none")
    node_loss_fn = nn.CrossEntropyLoss(weight=node_class_weights, reduction="none")
    loader = DataLoader(
        TensorDataset(
            train_x,
            tensors["train_source_node_x"],
            tensors["train_target_node_x"],
            train_y,
            train_weight,
        ),
        batch_size=max(1, int(args.batch_size)),
        shuffle=True,
    )
    node_loader = DataLoader(TensorDataset(train_node_x, train_node_y, train_node_weight), batch_size=max(1, int(args.batch_size)), shuffle=True)
    keep_loss_fn = None
    family_lookup = torch.tensor(_relation_family_indices(), dtype=torch.long, device=args.device)
    family_class_weights = _class_weights(
        family_lookup[train_y.to(args.device)],
        classes=len(GRAPH_PARSER_RELATION_FAMILIES),
        torch=torch,
        device=args.device,
    )
    family_loss_fn = nn.CrossEntropyLoss(weight=family_class_weights, reduction="none")
    if getattr(model, "keep_output", None) is not None:
        keep_targets = (train_y != GRAPH_PARSER_RELATIONS.index("NONE")).float()
        positive_count = float(keep_targets.sum().item())
        negative_count = float(keep_targets.numel() - positive_count)
        keep_pos_weight = max(1.0, min(100.0, negative_count / max(positive_count, 1.0)))
        keep_loss_fn = nn.BCEWithLogitsLoss(
            pos_weight=torch.tensor(keep_pos_weight, dtype=torch.float32, device=args.device),
            reduction="none",
        )
    for _epoch in range(max(1, int(args.epochs))):
        node_model.train()
        for batch_x, batch_y, batch_weight in node_loader:
            batch_x = batch_x.to(args.device)
            batch_y = batch_y.to(args.device)
            batch_weight = batch_weight.to(args.device)
            node_optimizer.zero_grad(set_to_none=True)
            loss = _weighted_loss(node_loss_fn(node_model(batch_x), batch_y), batch_weight, torch=torch)
            loss.backward()
            node_optimizer.step()
        model.train()
        for batch_x, batch_source_node_x, batch_target_node_x, batch_y, batch_weight in loader:
            batch_x = batch_x.to(args.device)
            batch_source_node_x = batch_source_node_x.to(args.device)
            batch_target_node_x = batch_target_node_x.to(args.device)
            batch_y = batch_y.to(args.device)
            batch_weight = batch_weight.to(args.device)
            optimizer.zero_grad(set_to_none=True)
            relation_logits = model(batch_x, batch_source_node_x, batch_target_node_x)
            relation_loss = _weighted_loss(
                loss_fn(relation_logits, batch_y),
                batch_weight,
                torch=torch,
            )
            loss = relation_loss
            if keep_loss_fn is not None:
                keep_logits = model.keep_logits(batch_x, batch_source_node_x, batch_target_node_x)
                keep_targets = (batch_y != GRAPH_PARSER_RELATIONS.index("NONE")).float()
                keep_loss = _weighted_loss(
                    keep_loss_fn(keep_logits, keep_targets),
                    batch_weight,
                    torch=torch,
                )
                loss = relation_loss + (float(args.keep_loss_weight) * keep_loss)
            if getattr(model, "family_output", None) is not None:
                family_logits = model.family_logits(batch_x, batch_source_node_x, batch_target_node_x)
                family_targets = family_lookup[batch_y]
                family_loss = _weighted_loss(
                    family_loss_fn(family_logits, family_targets),
                    batch_weight,
                    torch=torch,
                )
                loss = loss + 0.25 * family_loss
            loss.backward()
            optimizer.step()

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
        source_node_x=tensors["train_source_node_x"][: train_eval_x.shape[0]],
        target_node_x=tensors["train_target_node_x"][: train_eval_x.shape[0]],
        labels=GRAPH_PARSER_RELATIONS,
        positive_excluded_label="NONE",
        keep_threshold=float(args.keep_threshold),
        batch_size=int(args.eval_batch_size),
        device=args.device,
        torch=torch,
    )
    validation_eval = _evaluate_torch_model(
        model,
        val_x,
        val_y,
        source_node_x=tensors["val_source_node_x"],
        target_node_x=tensors["val_target_node_x"],
        labels=GRAPH_PARSER_RELATIONS,
        positive_excluded_label="NONE",
        keep_threshold=float(args.keep_threshold),
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
        "keep_validation": _evaluate_keep_head(
            model,
            val_x,
            tensors["val_source_node_x"],
            tensors["val_target_node_x"],
            val_y,
            keep_threshold=float(args.keep_threshold),
            batch_size=int(args.eval_batch_size),
            device=args.device,
            torch=torch,
        ),
        "family_validation": _evaluate_family_head(
            model,
            val_x,
            tensors["val_source_node_x"],
            tensors["val_target_node_x"],
            val_y,
            batch_size=int(args.eval_batch_size),
            device=args.device,
            torch=torch,
        ),
        "node_train": node_train_eval,
        "node_validation": node_validation_eval,
        "train_config": artifact.train_config,
        "architecture": artifact.train_config.get("architecture", {}),
        "tensor_cache": tensors.get("tensor_cache", {}),
        "notes": [
            "PyTorch is used only in the isolated training environment.",
            "Exported Graph Parser artifact is candidate-only until verifier/accepted gate passes.",
            "Relation training is vectorized over batched edge/source-node/target-node tensors.",
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


def _build_hidden_mlp(*, input_width: int, hidden_units: int, hidden_layers: int, nn: Any) -> Any:
    layers: list[Any] = []
    hidden_width = max(8, int(hidden_units))
    for layer_index in range(max(1, int(hidden_layers))):
        layers.append(nn.Linear(input_width if layer_index == 0 else hidden_width, hidden_width))
        layers.append(nn.ReLU())
    return nn.Sequential(*layers)


def _hidden_output_width(model: Any, *, fallback: int) -> int:
    for layer in reversed(list(model)):
        if hasattr(layer, "out_features"):
            return int(layer.out_features)
    return int(fallback)


def _build_node_context_model(*, input_width: int, output_width: int, hidden_units: int, hidden_layers: int, nn: Any) -> Any:
    class NodeContextModel(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.hidden = _build_hidden_mlp(
                input_width=input_width,
                hidden_units=hidden_units,
                hidden_layers=hidden_layers,
                nn=nn,
            )
            self.output = nn.Linear(_hidden_output_width(self.hidden, fallback=input_width), output_width)

        def encode(self, x: Any) -> Any:
            return self.hidden(x)

        def forward(self, x: Any) -> Any:
            return self.output(self.encode(x))

    return NodeContextModel()


def _build_context_relation_model(
    *,
    input_width: int,
    output_width: int,
    node_model: Any,
    node_output_width: int,
    hidden_units: int,
    hidden_layers: int,
    nn: Any,
    torch: Any,
    architecture_mode: str,
) -> Any:
    class ContextRelationModel(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.node_model = node_model
            self.architecture_mode = str(architecture_mode or "graph_parser_m4")
            self.edge_hidden = _build_hidden_mlp(
                input_width=input_width,
                hidden_units=hidden_units,
                hidden_layers=hidden_layers,
                nn=nn,
            )
            edge_width = _hidden_output_width(self.edge_hidden, fallback=input_width)
            node_width = _hidden_output_width(self.node_model.hidden, fallback=len(GRAPH_PARSER_NODE_FEATURES))
            interaction_multiplier = 2 if self.architecture_mode in {"graph_parser_m3", "graph_parser_m4"} else 0
            relation_width = edge_width + (2 * node_width) + (2 * int(node_output_width))
            relation_width += interaction_multiplier * (node_width + int(node_output_width))
            self.output = nn.Linear(relation_width, output_width)
            self.family_output = nn.Linear(relation_width, len(GRAPH_PARSER_RELATION_FAMILIES))
            self.keep_output = nn.Linear(relation_width, 1) if self.architecture_mode == "graph_parser_m4" else None

        def fused_context(self, edge_x: Any, source_node_x: Any, target_node_x: Any) -> Any:
            edge_context = self.edge_hidden(edge_x)
            source_context = self.node_model.encode(source_node_x)
            target_context = self.node_model.encode(target_node_x)
            source_logits = self.node_model.output(source_context)
            target_logits = self.node_model.output(target_context)
            pieces = [edge_context, source_context, target_context, source_logits, target_logits]
            if self.architecture_mode in {"graph_parser_m3", "graph_parser_m4"}:
                pieces.extend(
                    [
                        torch.abs(source_context - target_context),
                        source_context * target_context,
                        torch.abs(source_logits - target_logits),
                        source_logits * target_logits,
                    ]
                )
            return torch.cat(pieces, dim=1)

        def keep_logits(self, edge_x: Any, source_node_x: Any, target_node_x: Any) -> Any:
            if self.keep_output is None:
                raise RuntimeError("keep head unavailable")
            return self.keep_output(self.fused_context(edge_x, source_node_x, target_node_x)).squeeze(1)

        def family_logits(self, edge_x: Any, source_node_x: Any, target_node_x: Any) -> Any:
            return self.family_output(self.fused_context(edge_x, source_node_x, target_node_x))

        def forward(self, edge_x: Any, source_node_x: Any, target_node_x: Any) -> Any:
            return self.output(self.fused_context(edge_x, source_node_x, target_node_x))

    return ContextRelationModel()


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

    hidden_layers = [layer for layer in model.edge_hidden if isinstance(layer, nn.Linear)]
    output = model.output
    keep_output = getattr(model, "keep_output", None)
    node_hidden_layers = [layer for layer in node_model.hidden if isinstance(layer, nn.Linear)]
    node_output = node_model.output
    return TinyBDGraphParserArtifact(
        version=GRAPH_PARSER_ARTIFACT_VERSION,
        model_version=(
            "tinybdmath_graph_parser_m4"
            if str(args.relation_architecture) == "graph_parser_m4"
            else ("tinybdmath_graph_parser_m3" if str(args.relation_architecture) == "graph_parser_m3" else "tinybdmath_graph_parser_m2")
        ),
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
            "mode": str(args.relation_architecture),
            "epochs": int(args.epochs),
            "batch_size": int(args.batch_size),
            "hidden_units": int(args.hidden_units),
            "hidden_layers": int(args.hidden_layers),
            "learning_rate": float(args.learning_rate),
            "weight_decay": float(args.weight_decay),
            "validation_fraction": float(args.validation_fraction),
            "threshold": float(args.threshold),
            "node_filter_threshold": float(args.node_filter_threshold),
            "keep_threshold": float(args.keep_threshold),
            "keep_loss_weight": float(args.keep_loss_weight),
            "eval_batch_size": int(args.eval_batch_size),
            "max_train_eval_samples": int(args.max_train_eval_samples),
            "seed": int(args.seed),
            "device": str(args.device),
            "torch_version": str(torch.__version__),
            "architecture": {
                "relation_model": (
                    "interaction_relation_keep_mlp"
                    if str(args.relation_architecture) == "graph_parser_m4"
                    else ("interaction_relation_mlp" if str(args.relation_architecture) == "graph_parser_m3" else "context_relation_mlp")
                ),
                "relation_inputs": (
                    [
                        "edge_features",
                        "source_node_embedding",
                        "target_node_embedding",
                        "source_node_logits",
                        "target_node_logits",
                        "abs_node_embedding_delta",
                        "node_embedding_product",
                        "abs_node_logit_delta",
                        "node_logit_product",
                    ]
                    if str(args.relation_architecture) in {"graph_parser_m3", "graph_parser_m4"}
                    else ["edge_features", "source_node_embedding", "target_node_embedding", "source_node_logits", "target_node_logits"]
                ),
                "edge_presence_head": bool(str(args.relation_architecture) == "graph_parser_m4"),
                "coarse_relation_family_head": True,
                "node_model": "shared_node_context_mlp",
                "training_backend": "pytorch_vectorized_batches",
            },
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
        keep_output_weights=tuple(
            round(float(value), 8)
            for value in (keep_output.weight.detach().cpu().view(-1).tolist() if keep_output is not None else [])
        ),
        keep_output_bias=round(float(keep_output.bias.detach().cpu().view(-1)[0].item()), 8) if keep_output is not None else 0.0,
        keep_threshold=float(args.keep_threshold),
    )


def _load_training_tensors_cached(args: argparse.Namespace, *, torch: Any) -> dict[str, Any]:
    cache_dir = Path(args.tensor_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _tensor_cache_key(args)
    cache_path = cache_dir / f"{cache_key}.pt"
    if cache_path.exists():
        payload = torch.load(cache_path, map_location="cpu")
        if isinstance(payload, dict):
            payload["tensor_cache"] = {
                "enabled": True,
                "hit": True,
                "path": str(cache_path),
                "key": cache_key,
            }
            return payload
    started = time.perf_counter()
    payload = _load_training_tensors_streaming(args, torch=torch)
    payload["tensor_cache"] = {
        "enabled": True,
        "hit": False,
        "path": str(cache_path),
        "key": cache_key,
        "build_seconds": round(time.perf_counter() - started, 6),
    }
    torch.save(payload, cache_path)
    return payload


def _tensor_cache_key(args: argparse.Namespace) -> str:
    payload = {
        "schema_version": "tinybdmath_graph_parser_tensor_cache_v1",
        "graph_rows": _path_cache_fingerprint(Path(args.graph_rows)),
        "alignment_rows": _path_cache_fingerprint(Path(args.alignment_rows)),
        "limit": int(args.limit),
        "validation_fraction": float(args.validation_fraction),
        "seed": int(args.seed),
        "feature_version": GRAPH_PARSER_FEATURE_VERSION,
        "feature_names": list(GRAPH_PARSER_FEATURES),
        "node_feature_names": list(GRAPH_PARSER_NODE_FEATURES),
        "relation_labels": list(GRAPH_PARSER_RELATIONS),
        "node_label_names": list(GRAPH_PARSER_NODE_LABELS),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _path_cache_fingerprint(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _load_training_tensors_streaming(args: argparse.Namespace, *, torch: Any) -> dict[str, Any]:
    train_chunks: list[Any] = []
    train_y_chunks: list[Any] = []
    train_weight_chunks: list[Any] = []
    train_source_node_chunks: list[Any] = []
    train_target_node_chunks: list[Any] = []
    val_chunks: list[Any] = []
    val_y_chunks: list[Any] = []
    val_source_node_chunks: list[Any] = []
    val_target_node_chunks: list[Any] = []
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
        _append_relation_tensor_chunk(
            sample_train,
            feature_names=GRAPH_PARSER_FEATURES,
            node_feature_names=GRAPH_PARSER_NODE_FEATURES,
            labels=GRAPH_PARSER_RELATIONS,
            label_key="relation",
            x_chunks=train_chunks,
            source_node_x_chunks=train_source_node_chunks,
            target_node_x_chunks=train_target_node_chunks,
            y_chunks=train_y_chunks,
            weight_chunks=train_weight_chunks,
            torch=torch,
        )
        _append_relation_tensor_chunk(
            sample_val,
            feature_names=GRAPH_PARSER_FEATURES,
            node_feature_names=GRAPH_PARSER_NODE_FEATURES,
            labels=GRAPH_PARSER_RELATIONS,
            label_key="relation",
            x_chunks=val_chunks,
            source_node_x_chunks=val_source_node_chunks,
            target_node_x_chunks=val_target_node_chunks,
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
    train_source_node_raw = _cat_or_empty(train_source_node_chunks, width=len(GRAPH_PARSER_NODE_FEATURES), torch=torch)
    train_target_node_raw = _cat_or_empty(train_target_node_chunks, width=len(GRAPH_PARSER_NODE_FEATURES), torch=torch)
    val_source_node_raw = _cat_or_empty(val_source_node_chunks, width=len(GRAPH_PARSER_NODE_FEATURES), torch=torch)
    val_target_node_raw = _cat_or_empty(val_target_node_chunks, width=len(GRAPH_PARSER_NODE_FEATURES), torch=torch)
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
    train_source_node_x, _unused_source_node_means, _unused_source_node_scales = _normalize_raw_tensor(
        train_source_node_raw,
        means=node_means,
        scales=node_scales,
        torch=torch,
    )
    train_target_node_x, _unused_target_node_means, _unused_target_node_scales = _normalize_raw_tensor(
        train_target_node_raw,
        means=node_means,
        scales=node_scales,
        torch=torch,
    )
    val_source_node_x, _unused_val_source_node_means, _unused_val_source_node_scales = _normalize_raw_tensor(
        val_source_node_raw,
        means=node_means,
        scales=node_scales,
        torch=torch,
    )
    val_target_node_x, _unused_val_target_node_means, _unused_val_target_node_scales = _normalize_raw_tensor(
        val_target_node_raw,
        means=node_means,
        scales=node_scales,
        torch=torch,
    )
    return {
        "samples": total_samples,
        "node_samples": total_node_samples,
        "train_x": train_x,
        "train_source_node_x": train_source_node_x,
        "train_target_node_x": train_target_node_x,
        "train_y": train_y,
        "train_weight": train_weight,
        "val_x": val_x,
        "val_source_node_x": val_source_node_x,
        "val_target_node_x": val_target_node_x,
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


def _append_relation_tensor_chunk(
    samples: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    node_feature_names: tuple[str, ...],
    labels: tuple[str, ...],
    label_key: str,
    x_chunks: list[Any],
    source_node_x_chunks: list[Any],
    target_node_x_chunks: list[Any],
    y_chunks: list[Any],
    weight_chunks: list[Any] | None,
    torch: Any,
) -> None:
    if not samples:
        return
    x, source_node_x, target_node_x, y, weights = _samples_to_relation_raw_tensors(
        samples,
        feature_names=feature_names,
        node_feature_names=node_feature_names,
        labels=labels,
        label_key=label_key,
        torch=torch,
    )
    x_chunks.append(x)
    source_node_x_chunks.append(source_node_x)
    target_node_x_chunks.append(target_node_x)
    y_chunks.append(y)
    if weight_chunks is not None:
        weight_chunks.append(weights)


def _samples_to_relation_raw_tensors(
    samples: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    node_feature_names: tuple[str, ...],
    labels: tuple[str, ...],
    label_key: str,
    torch: Any,
) -> tuple[Any, Any, Any, Any, Any]:
    label_to_index = {label: index for index, label in enumerate(labels)}
    feature_rows: list[list[float]] = []
    source_node_rows: list[list[float]] = []
    target_node_rows: list[list[float]] = []
    label_values: list[int] = []
    weights: list[float] = []
    for sample in samples:
        features = sample.get("features", {})
        if not isinstance(features, dict):
            features = {}
        source_node_features = sample.get("source_node_features", {})
        if not isinstance(source_node_features, dict):
            source_node_features = {}
        target_node_features = sample.get("target_node_features", {})
        if not isinstance(target_node_features, dict):
            target_node_features = {}
        feature_rows.append([float(features.get(name, 0.0) or 0.0) for name in feature_names])
        source_node_rows.append([float(source_node_features.get(name, 0.0) or 0.0) for name in node_feature_names])
        target_node_rows.append([float(target_node_features.get(name, 0.0) or 0.0) for name in node_feature_names])
        label_values.append(label_to_index.get(str(sample.get(label_key, labels[0])), 0))
        weights.append(_sample_weight(sample))
    return (
        torch.tensor(feature_rows, dtype=torch.float32),
        torch.tensor(source_node_rows, dtype=torch.float32),
        torch.tensor(target_node_rows, dtype=torch.float32),
        torch.tensor(label_values, dtype=torch.long),
        torch.tensor(weights, dtype=torch.float32),
    )


def _samples_to_raw_tensors(
    samples: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    labels: tuple[str, ...],
    label_key: str,
    torch: Any,
) -> tuple[Any, Any, Any]:
    label_to_index = {label: index for index, label in enumerate(labels)}
    feature_rows: list[list[float]] = []
    label_values: list[int] = []
    weights: list[float] = []
    for sample in samples:
        features = sample.get("features", {})
        if not isinstance(features, dict):
            features = {}
        feature_rows.append([float(features.get(name, 0.0) or 0.0) for name in feature_names])
        label_values.append(label_to_index.get(str(sample.get(label_key, labels[0])), 0))
        weights.append(_sample_weight(sample))
    return (
        torch.tensor(feature_rows, dtype=torch.float32),
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


def _relation_family_indices() -> list[int]:
    family_to_index = {label: index for index, label in enumerate(GRAPH_PARSER_RELATION_FAMILIES)}
    return [
        family_to_index[GRAPH_PARSER_RELATION_TO_FAMILY.get(label, "NONE")]
        for label in GRAPH_PARSER_RELATIONS
    ]


def _limit_eval_tensors(x: Any, y: Any, *, max_samples: int) -> tuple[Any, Any]:
    if max_samples <= 0 or x.shape[0] <= max_samples:
        return x, y
    return x[:max_samples], y[:max_samples]


def _evaluate_torch_model(
    model: Any,
    x: Any,
    y: Any,
    *,
    source_node_x: Any | None = None,
    target_node_x: Any | None = None,
    labels: tuple[str, ...],
    batch_size: int,
    device: str,
    torch: Any,
    positive_excluded_label: str | None = None,
    keep_threshold: float = 0.5,
) -> dict[str, Any]:
    model.eval()
    class_count = len(labels)
    none_index = labels.index(positive_excluded_label) if positive_excluded_label is not None and positive_excluded_label in labels else -1
    label_counts = torch.bincount(y.cpu(), minlength=class_count).long()
    predicted_counts = torch.zeros(class_count, dtype=torch.long)
    correct_by_label = torch.zeros(class_count, dtype=torch.long)
    correct = 0
    total = int(y.numel())
    with torch.no_grad():
        for start in range(0, total, max(1, int(batch_size))):
            end = min(total, start + max(1, int(batch_size)))
            batch_x = x[start:end].to(device)
            if source_node_x is not None and target_node_x is not None:
                batch_source_node_x = source_node_x[start:end].to(device)
                batch_target_node_x = target_node_x[start:end].to(device)
                logits = model(batch_x, batch_source_node_x, batch_target_node_x)
                predicted = _relation_predictions_from_logits(
                    model,
                    logits,
                    batch_x,
                    batch_source_node_x,
                    batch_target_node_x,
                    none_index=none_index,
                    keep_threshold=keep_threshold,
                    torch=torch,
                ).cpu()
            else:
                logits = model(batch_x)
                predicted = logits.argmax(dim=1).cpu()
            expected = y[start:end].cpu()
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


def _evaluate_keep_head(
    model: Any,
    x: Any,
    source_node_x: Any,
    target_node_x: Any,
    y: Any,
    *,
    keep_threshold: float,
    batch_size: int,
    device: str,
    torch: Any,
) -> dict[str, Any]:
    if getattr(model, "keep_output", None) is None:
        return {}
    model.eval()
    total = int(y.numel())
    positive_total = 0
    negative_total = 0
    true_positive = 0
    true_negative = 0
    predicted_positive = 0
    with torch.no_grad():
        for start in range(0, total, max(1, int(batch_size))):
            end = min(total, start + max(1, int(batch_size)))
            batch_x = x[start:end].to(device)
            batch_source_node_x = source_node_x[start:end].to(device)
            batch_target_node_x = target_node_x[start:end].to(device)
            keep_logits = model.keep_logits(batch_x, batch_source_node_x, batch_target_node_x)
            keep_prob = torch.sigmoid(keep_logits).cpu()
            expected = (y[start:end] != GRAPH_PARSER_RELATIONS.index("NONE")).cpu()
            predicted = keep_prob >= float(keep_threshold)
            positive_total += int(expected.sum().item())
            negative_total += int((~expected).sum().item())
            predicted_positive += int(predicted.sum().item())
            true_positive += int((predicted & expected).sum().item())
            true_negative += int(((~predicted) & (~expected)).sum().item())
    accuracy = (true_positive + true_negative) / total if total else 0.0
    positive_recall = true_positive / positive_total if positive_total else 0.0
    positive_precision = true_positive / predicted_positive if predicted_positive else 0.0
    return {
        "accuracy": round(float(accuracy), 6),
        "positive_recall": round(float(positive_recall), 6),
        "positive_precision": round(float(positive_precision), 6),
        "predicted_positive_rate": round(float(predicted_positive / total), 6) if total else 0.0,
        "threshold": float(keep_threshold),
    }


def _evaluate_family_head(
    model: Any,
    x: Any,
    source_node_x: Any,
    target_node_x: Any,
    y: Any,
    *,
    batch_size: int,
    device: str,
    torch: Any,
) -> dict[str, Any]:
    if getattr(model, "family_output", None) is None:
        return {}
    model.eval()
    family_lookup = torch.tensor(_relation_family_indices(), dtype=torch.long, device=device)
    class_count = len(GRAPH_PARSER_RELATION_FAMILIES)
    expected_cpu = family_lookup[y.to(device)].cpu()
    label_counts = torch.bincount(expected_cpu, minlength=class_count).long()
    predicted_counts = torch.zeros(class_count, dtype=torch.long)
    correct_by_label = torch.zeros(class_count, dtype=torch.long)
    correct = 0
    total = int(expected_cpu.numel())
    with torch.no_grad():
        for start in range(0, total, max(1, int(batch_size))):
            end = min(total, start + max(1, int(batch_size)))
            batch_x = x[start:end].to(device)
            batch_source_node_x = source_node_x[start:end].to(device)
            batch_target_node_x = target_node_x[start:end].to(device)
            logits = model.family_logits(batch_x, batch_source_node_x, batch_target_node_x)
            predicted = logits.argmax(dim=1).cpu()
            expected = expected_cpu[start:end]
            predicted_counts += torch.bincount(predicted, minlength=class_count).long()
            matched = predicted == expected
            correct += int(matched.sum().item())
            if bool(matched.any()):
                correct_by_label += torch.bincount(expected[matched], minlength=class_count).long()
    return {
        "accuracy": round(correct / total, 6) if total else 0.0,
        "label_counts": _label_count_report(label_counts, GRAPH_PARSER_RELATION_FAMILIES),
        "predicted_counts": _label_count_report(predicted_counts, GRAPH_PARSER_RELATION_FAMILIES),
        "per_label_recall": _tensor_per_label_recall(label_counts, correct_by_label, GRAPH_PARSER_RELATION_FAMILIES),
        "per_label_precision": _tensor_per_label_precision(predicted_counts, correct_by_label, GRAPH_PARSER_RELATION_FAMILIES),
    }


def _relation_predictions_from_logits(
    model: Any,
    logits: Any,
    edge_x: Any,
    source_node_x: Any,
    target_node_x: Any,
    *,
    none_index: int,
    keep_threshold: float,
    torch: Any,
) -> Any:
    if getattr(model, "keep_output", None) is None or none_index < 0:
        return logits.argmax(dim=1)
    probabilities = torch.softmax(logits, dim=1)
    keep_prob = torch.sigmoid(model.keep_logits(edge_x, source_node_x, target_node_x))
    positive = probabilities.clone()
    positive[:, none_index] = 0.0
    positive_mass = positive.sum(dim=1, keepdim=True).clamp_min(1e-12)
    conditional_positive = positive / positive_mass
    best_positive = conditional_positive.argmax(dim=1)
    keep_mask = keep_prob >= float(keep_threshold)
    return torch.where(keep_mask, best_positive, torch.full_like(best_positive, none_index))


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
