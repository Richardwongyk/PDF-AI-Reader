"""TinyBDMath Graph Parser M1 artifact and pure-Python inference.

Training uses PyTorch in the isolated science environment.  The main app loads
the exported JSON artifact and performs candidate-only inference without a
PyTorch runtime dependency.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any


GRAPH_PARSER_ARTIFACT_VERSION = "tinybdmath_graph_parser_m1_json_v1"
GRAPH_PARSER_FEATURE_VERSION = "tinybdmath_graph_parser_features_v1"

GRAPH_PARSER_RELATIONS = (
    "NONE",
    "NEXT",
    "SUB",
    "SUP",
    "NUMERATOR",
    "DENOMINATOR",
    "RADICAL_BODY",
    "RADICAL_INDEX",
    "BASE",
    "CHILD",
    "FENCE_BODY",
    "FENCE_OPEN",
    "FENCE_CLOSE",
    "MATRIX_ROW",
    "MATRIX_CELL",
    "CELL_CONTENT",
)

GRAPH_PARSER_FEATURES = (
    "bias",
    "dx",
    "dy",
    "abs_dx",
    "abs_dy",
    "distance",
    "x_overlap",
    "y_overlap",
    "source_width",
    "source_height",
    "target_width",
    "target_height",
    "size_ratio",
    "source_is_math",
    "target_is_math",
    "source_is_rule",
    "target_is_rule",
    "same_font",
    "source_index",
    "target_index",
)


@dataclass(frozen=True)
class TinyBDGraphParserPrediction:
    source: str
    target: str
    relation: str
    confidence: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TinyBDGraphParserArtifact:
    version: str
    model_version: str
    feature_version: str
    feature_names: tuple[str, ...]
    relation_labels: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    hidden_weights: tuple[tuple[tuple[float, ...], ...], ...]
    hidden_biases: tuple[tuple[float, ...], ...]
    output_weights: tuple[tuple[float, ...], ...]
    output_bias: tuple[float, ...]
    threshold: float
    train_config: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "TinyBDGraphParserArtifact":
        return cls(
            version=str(payload.get("version", "") or GRAPH_PARSER_ARTIFACT_VERSION),
            model_version=str(payload.get("model_version", "") or "tinybdmath_graph_parser_m1"),
            feature_version=str(payload.get("feature_version", "") or GRAPH_PARSER_FEATURE_VERSION),
            feature_names=tuple(str(item) for item in payload.get("feature_names", GRAPH_PARSER_FEATURES)),
            relation_labels=tuple(str(item) for item in payload.get("relation_labels", GRAPH_PARSER_RELATIONS)),
            means=tuple(float(item) for item in payload.get("means", [])),
            scales=tuple(float(item) for item in payload.get("scales", [])),
            hidden_weights=tuple(
                tuple(tuple(float(value) for value in row) for row in layer)
                for layer in payload.get("hidden_weights", [])
            ),
            hidden_biases=tuple(
                tuple(float(value) for value in layer)
                for layer in payload.get("hidden_biases", [])
            ),
            output_weights=tuple(tuple(float(value) for value in row) for row in payload.get("output_weights", [])),
            output_bias=tuple(float(value) for value in payload.get("output_bias", [])),
            threshold=float(payload.get("threshold", 0.50) or 0.50),
            train_config=dict(payload.get("train_config", {}) or {}),
        )

    @classmethod
    def load(cls, path: Path) -> "TinyBDGraphParserArtifact":
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")


class TinyBDGraphParser:
    def __init__(self, artifact: TinyBDGraphParserArtifact) -> None:
        self.artifact = artifact

    @classmethod
    def load(cls, path: Path) -> "TinyBDGraphParser":
        return cls(TinyBDGraphParserArtifact.load(path))

    def predict_row(self, graph_row: dict[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
        nodes = graph_nodes(graph_row)
        pairs = candidate_pairs(nodes)
        predictions: list[TinyBDGraphParserPrediction] = []
        cutoff = self.artifact.threshold if threshold is None else float(threshold)
        for source, target in pairs:
            features = graph_parser_features(source, target, nodes)
            probabilities = self._predict_probabilities(features)
            if not probabilities:
                continue
            best_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
            relation = self.artifact.relation_labels[best_index]
            confidence = float(probabilities[best_index])
            if relation == "NONE" or confidence < cutoff:
                continue
            predictions.append(
                TinyBDGraphParserPrediction(
                    source=str(source["node_id"]),
                    target=str(target["node_id"]),
                    relation=relation,
                    confidence=round(confidence, 6),
                )
            )
        return {
            "schema_version": "tinybdmath_graph_parser_predictions_v1",
            "model_version": self.artifact.model_version,
            "feature_version": self.artifact.feature_version,
            "input_hash": _stable_hash({"graph_input_hash": graph_row.get("input_hash", ""), "model": self.artifact.model_version}),
            "node_count": len(nodes),
            "candidate_pairs": len(pairs),
            "predictions": [item.to_json() for item in sorted(predictions, key=lambda item: (item.source, item.target, item.relation))],
            "candidate_only": True,
        }

    def _predict_probabilities(self, features: dict[str, float]) -> list[float]:
        values = [
            (float(features.get(name, 0.0)) - mean) / (scale if abs(scale) > 1e-12 else 1.0)
            for name, mean, scale in zip(self.artifact.feature_names, self.artifact.means, self.artifact.scales)
        ]
        activations = values
        for weights, biases in zip(self.artifact.hidden_weights, self.artifact.hidden_biases):
            next_values: list[float] = []
            for row, bias in zip(weights, biases):
                value = float(bias) + sum(float(weight) * item for weight, item in zip(row, activations))
                next_values.append(max(0.0, value))
            activations = next_values
        logits = [
            float(bias) + sum(float(weight) * item for weight, item in zip(row, activations))
            for row, bias in zip(self.artifact.output_weights, self.artifact.output_bias)
        ]
        return _softmax(logits)


def graph_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, item in enumerate(row.get("glyph_nodes", []) or []):
        if isinstance(item, dict):
            node = _normalize_node(item, index=index, node_type="glyph")
            if str(node.get("text", "")).strip():
                nodes.append(node)
    offset = len(nodes)
    for index, item in enumerate(row.get("vector_nodes", []) or []):
        if isinstance(item, dict):
            nodes.append(_normalize_node(item, index=offset + index, node_type="vector"))
    return sorted(nodes, key=lambda item: (item["bbox"][0], item["bbox"][1], item["node_id"]))


def candidate_pairs(nodes: list[dict[str, Any]], *, max_neighbors: int = 16) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ordered = sorted(nodes, key=lambda item: (item["bbox"][0], item["bbox"][1], item["node_id"]))
    for source_index, source in enumerate(ordered):
        local: list[tuple[float, dict[str, Any]]] = []
        for target_index, target in enumerate(ordered):
            if source["node_id"] == target["node_id"]:
                continue
            dx = float(target["center"][0]) - float(source["center"][0])
            dy = float(target["center"][1]) - float(source["center"][1])
            distance = math.sqrt(dx * dx + dy * dy)
            rank_penalty = abs(target_index - source_index) * 0.25
            local.append((distance + rank_penalty, target))
        for _score, target in sorted(local, key=lambda item: (item[0], item[1]["node_id"]))[:max_neighbors]:
            pairs.append((source, target))
    return pairs


def graph_parser_features(source: dict[str, Any], target: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, float]:
    sx0, sy0, sx1, sy1 = source["bbox"]
    tx0, ty0, tx1, ty1 = target["bbox"]
    sw = max(1e-6, sx1 - sx0)
    sh = max(1e-6, sy1 - sy0)
    tw = max(1e-6, tx1 - tx0)
    th = max(1e-6, ty1 - ty0)
    dx = float(target["center"][0]) - float(source["center"][0])
    dy = float(target["center"][1]) - float(source["center"][1])
    norm = max(sw, sh, tw, th, 1.0)
    source_index = float(source.get("order_index", 0))
    target_index = float(target.get("order_index", 0))
    return {
        "bias": 1.0,
        "dx": dx / norm,
        "dy": dy / norm,
        "abs_dx": abs(dx) / norm,
        "abs_dy": abs(dy) / norm,
        "distance": math.sqrt(dx * dx + dy * dy) / norm,
        "x_overlap": _overlap(sx0, sx1, tx0, tx1) / max(sw, tw, 1e-6),
        "y_overlap": _overlap(sy0, sy1, ty0, ty1) / max(sh, th, 1e-6),
        "source_width": sw / norm,
        "source_height": sh / norm,
        "target_width": tw / norm,
        "target_height": th / norm,
        "size_ratio": min(sw * sh, tw * th) / max(sw * sh, tw * th, 1e-6),
        "source_is_math": 1.0 if source.get("is_math_font") else 0.0,
        "target_is_math": 1.0 if target.get("is_math_font") else 0.0,
        "source_is_rule": 1.0 if source.get("node_type") == "vector" else 0.0,
        "target_is_rule": 1.0 if target.get("node_type") == "vector" else 0.0,
        "same_font": 1.0 if str(source.get("font", "")) == str(target.get("font", "")) else 0.0,
        "source_index": source_index / max(1.0, len(nodes) - 1),
        "target_index": target_index / max(1.0, len(nodes) - 1),
    }


def training_samples_from_rows(
    graph_rows: list[dict[str, Any]],
    alignment_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alignment_by_row_id = {str(row.get("row_id", "") or ""): row for row in alignment_rows}
    samples: list[dict[str, Any]] = []
    for row in graph_rows:
        row_id = str(row.get("row_id", "") or "")
        alignment = alignment_by_row_id.get(row_id)
        if alignment is None:
            continue
        positive: dict[tuple[str, str], tuple[str, float]] = {}
        for label in alignment.get("relation_labels", []) or []:
            if not isinstance(label, dict):
                continue
            source = str(label.get("source", "") or "")
            target = str(label.get("target", "") or "")
            relation = str(label.get("relation", "") or "")
            if relation not in GRAPH_PARSER_RELATIONS or relation == "NONE":
                continue
            confidence = float(label.get("confidence", 0.0) or 0.0)
            current = positive.get((source, target))
            if current is None or confidence > current[1]:
                positive[(source, target)] = (relation, confidence)
        nodes = graph_nodes(row)
        for source, target in candidate_pairs(nodes):
            key = (str(source["node_id"]), str(target["node_id"]))
            relation, confidence = positive.get(key, ("NONE", 1.0))
            samples.append(
                {
                    "row_id": row_id,
                    "source": key[0],
                    "target": key[1],
                    "relation": relation,
                    "confidence": confidence,
                    "features": graph_parser_features(source, target, nodes),
                }
            )
    return samples


def graph_parser_predictions_to_structural_candidate(predictions: dict[str, Any]) -> dict[str, Any]:
    relation_map = {
        "NEXT": "HORIZONTAL",
        "SUB": "SUB",
        "SUP": "SUP",
        "RADICAL_BODY": "RADICAL_BODY",
        "NUMERATOR": "ABOVE",
        "DENOMINATOR": "BELOW",
    }
    selected = []
    for index, item in enumerate(predictions.get("predictions", []) or []):
        if not isinstance(item, dict):
            continue
        relation = relation_map.get(str(item.get("relation", "") or ""))
        if relation is None:
            continue
        selected.append(
            {
                "edge_id": f"gp{index:05d}",
                "source": str(item.get("source", "") or ""),
                "target": str(item.get("target", "") or ""),
                "relation": relation,
                "confidence": float(item.get("confidence", 0.0) or 0.0),
                "hint": "graph_parser",
                "reason": "graph_parser_m1_prediction",
            }
        )
    return {
        "candidate_only": True,
        "abstain": not bool(selected),
        "selected_relations": selected,
        "verifier_warnings": [] if selected else ["graph_parser_no_selected_relations"],
        "model_version": str(predictions.get("model_version", "") or ""),
    }


def _normalize_node(item: dict[str, Any], *, index: int, node_type: str) -> dict[str, Any]:
    bbox = item.get("bbox", [0.0, 0.0, 0.0, 0.0])
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        bbox = [0.0, 0.0, 0.0, 0.0]
    bbox_values = [float(value or 0.0) for value in bbox]
    return {
        "node_id": str(item.get("node_id", "") or f"{node_type}{index:04d}"),
        "node_type": str(item.get("node_type", "") or node_type),
        "text": str(item.get("unicode", "") or item.get("text", "") or ""),
        "latex": str(item.get("latex", "") or item.get("text", "") or ""),
        "font": str(item.get("font", "") or item.get("normalized_font", "") or ""),
        "bbox": bbox_values,
        "center": [
            (bbox_values[0] + bbox_values[2]) / 2.0,
            (bbox_values[1] + bbox_values[3]) / 2.0,
        ],
        "is_math_font": bool(item.get("is_math_font", False)),
        "order_index": index,
    }


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    max_logit = max(logits)
    exps = [math.exp(max(-80.0, min(80.0, value - max_logit))) for value in logits]
    total = sum(exps) or 1.0
    return [value / total for value in exps]


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8",
        errors="ignore",
    )
    return hashlib.sha256(encoded).hexdigest()
