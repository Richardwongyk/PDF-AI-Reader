"""TinyBDMath Graph Parser M1 artifact and pure-Python inference.

Training uses PyTorch in the isolated science environment.  The main app loads
the exported JSON artifact and performs candidate-only inference without a
PyTorch runtime dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any
import unicodedata


GRAPH_PARSER_ARTIFACT_VERSION = "tinybdmath_graph_parser_m1_json_v1"
GRAPH_PARSER_FEATURE_VERSION = "tinybdmath_graph_parser_features_v4"

GRAPH_PARSER_RELATIONS = (
    "NONE",
    "NEXT",
    "SUB",
    "SUP",
    "PRE_SUB",
    "PRE_SUP",
    "NUMERATOR",
    "DENOMINATOR",
    "RADICAL_BODY",
    "RADICAL_INDEX",
    "BASE",
    "UNDER",
    "OVER",
    "ACCENT_BASE",
    "CHILD",
    "FENCE_BODY",
    "FENCE_OPEN",
    "FENCE_CLOSE",
    "MATRIX_ROW",
    "MATRIX_CELL",
    "CELL_CONTENT",
    "TEXT_RUN_NEXT",
    "ENCLOSURE_BODY",
    "EQUATION_TAG",
    "FRACTION_BAR",
    "OVERLINE",
    "UNDERLINE",
    "ABOVE",
    "BELOW",
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
    "source_is_horizontal_rule",
    "source_is_vertical_rule",
    "target_is_horizontal_rule",
    "target_is_vertical_rule",
    "source_aspect_ratio",
    "target_aspect_ratio",
    "same_font",
    "source_index",
    "target_index",
)

GRAPH_PARSER_NODE_LABELS = (
    "UNKNOWN",
    "SYMBOL",
    "TEXT",
    "OPERATOR",
    "SPACING",
    "HORIZONTAL_RULE",
    "VERTICAL_RULE",
    "EQUATION_TAG",
)
GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD = 0.80
GRAPH_PARSER_OPERATOR_KATEX_TYPES = (
    "op",
    "bin",
    "rel",
    "open",
    "close",
    "punct",
)

GRAPH_PARSER_NODE_FEATURES = (
    "bias",
    "width",
    "height",
    "area",
    "aspect_ratio",
    "relative_width",
    "relative_height",
    "x_span_ratio",
    "y_span_ratio",
    "center_x",
    "center_y",
    "text_length",
    "text_is_blank",
    "text_is_single_char",
    "text_is_ascii",
    "text_has_letter",
    "text_has_number",
    "text_has_symbol",
    "text_has_punctuation",
    "text_has_separator",
    "text_has_mark",
    "is_math",
    "is_rule",
    "is_horizontal_rule",
    "is_vertical_rule",
    "rule_aspect_ratio",
    "font_size",
    "font_size_ratio",
    "order_index",
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
    node_feature_names: tuple[str, ...] = GRAPH_PARSER_NODE_FEATURES
    node_label_names: tuple[str, ...] = GRAPH_PARSER_NODE_LABELS
    node_means: tuple[float, ...] = ()
    node_scales: tuple[float, ...] = ()
    node_hidden_weights: tuple[tuple[tuple[float, ...], ...], ...] = field(default_factory=tuple)
    node_hidden_biases: tuple[tuple[float, ...], ...] = field(default_factory=tuple)
    node_output_weights: tuple[tuple[float, ...], ...] = ()
    node_output_bias: tuple[float, ...] = ()
    node_filter_threshold: float = GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD

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
            node_feature_names=tuple(str(item) for item in payload.get("node_feature_names", GRAPH_PARSER_NODE_FEATURES)),
            node_label_names=tuple(str(item) for item in payload.get("node_label_names", GRAPH_PARSER_NODE_LABELS)),
            node_means=tuple(float(item) for item in payload.get("node_means", [])),
            node_scales=tuple(float(item) for item in payload.get("node_scales", [])),
            node_hidden_weights=tuple(
                tuple(tuple(float(value) for value in row) for row in layer)
                for layer in payload.get("node_hidden_weights", [])
            ),
            node_hidden_biases=tuple(
                tuple(float(value) for value in layer)
                for layer in payload.get("node_hidden_biases", [])
            ),
            node_output_weights=tuple(tuple(float(value) for value in row) for row in payload.get("node_output_weights", [])),
            node_output_bias=tuple(float(value) for value in payload.get("node_output_bias", [])),
            node_filter_threshold=float(
                payload.get("node_filter_threshold", GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD)
                or GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD
            ),
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
        all_nodes = graph_nodes(graph_row, include_blank=True)
        nodes = [node for node in all_nodes if node.get("node_type") == "vector" or str(node.get("text", "") or "").strip()]
        pairs = candidate_pairs(nodes)
        predictions: list[TinyBDGraphParserPrediction] = []
        relation_alternatives: list[dict[str, Any]] = []
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
            relation_alternatives.append(
                {
                    "source": str(source["node_id"]),
                    "target": str(target["node_id"]),
                    "alternatives": _top_relation_alternatives(
                        probabilities,
                        self.artifact.relation_labels,
                    ),
                }
            )
            predictions.append(
                TinyBDGraphParserPrediction(
                    source=str(source["node_id"]),
                    target=str(target["node_id"]),
                    relation=relation,
                    confidence=round(confidence, 6),
                )
            )
        node_predictions = self._predict_node_labels(all_nodes)
        return {
            "schema_version": "tinybdmath_graph_parser_predictions_v1",
            "model_version": self.artifact.model_version,
            "feature_version": self.artifact.feature_version,
            "input_hash": _stable_hash({"graph_input_hash": graph_row.get("input_hash", ""), "model": self.artifact.model_version}),
            "node_count": len(nodes),
            "candidate_pairs": len(pairs),
            "node_predictions": node_predictions,
            "node_filter_threshold": float(self.artifact.node_filter_threshold),
            "predictions": [item.to_json() for item in sorted(predictions, key=lambda item: (item.source, item.target, item.relation))],
            "relation_alternatives": sorted(
                relation_alternatives,
                key=lambda item: (str(item.get("source", "")), str(item.get("target", ""))),
            ),
            "candidate_only": True,
        }

    def _predict_probabilities(self, features: dict[str, float]) -> list[float]:
        return _feed_forward_probabilities(
            features,
            feature_names=self.artifact.feature_names,
            means=self.artifact.means,
            scales=self.artifact.scales,
            hidden_weights=self.artifact.hidden_weights,
            hidden_biases=self.artifact.hidden_biases,
            output_weights=self.artifact.output_weights,
            output_bias=self.artifact.output_bias,
        )

    def _predict_node_probabilities(self, features: dict[str, float]) -> list[float]:
        if not self.artifact.node_output_weights:
            return []
        return _feed_forward_probabilities(
            features,
            feature_names=self.artifact.node_feature_names,
            means=self.artifact.node_means,
            scales=self.artifact.node_scales,
            hidden_weights=self.artifact.node_hidden_weights,
            hidden_biases=self.artifact.node_hidden_biases,
            output_weights=self.artifact.node_output_weights,
            output_bias=self.artifact.node_output_bias,
        )

    def _predict_node_labels(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.artifact.node_output_weights:
            return []
        output: list[dict[str, Any]] = []
        for node in nodes:
            probabilities = self._predict_node_probabilities(graph_parser_node_features(node, nodes))
            if not probabilities:
                continue
            best_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
            label = self.artifact.node_label_names[best_index]
            output.append(
                {
                    "node_id": str(node.get("node_id", "") or ""),
                    "label": label,
                    "confidence": round(float(probabilities[best_index]), 6),
                }
            )
        return output


def _feed_forward_probabilities(
    features: dict[str, float],
    *,
    feature_names: tuple[str, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
    hidden_weights: tuple[tuple[tuple[float, ...], ...], ...],
    hidden_biases: tuple[tuple[float, ...], ...],
    output_weights: tuple[tuple[float, ...], ...],
    output_bias: tuple[float, ...],
) -> list[float]:
    if not output_weights:
        return []
    padded_means = means or tuple(0.0 for _ in feature_names)
    padded_scales = scales or tuple(1.0 for _ in feature_names)
    if len(padded_means) < len(feature_names):
        padded_means = tuple(padded_means) + tuple(0.0 for _ in range(len(feature_names) - len(padded_means)))
    if len(padded_scales) < len(feature_names):
        padded_scales = tuple(padded_scales) + tuple(1.0 for _ in range(len(feature_names) - len(padded_scales)))
    values = [
        (float(features.get(name, 0.0)) - mean) / (scale if abs(scale) > 1e-12 else 1.0)
        for name, mean, scale in zip(feature_names, padded_means, padded_scales)
    ]
    activations = values
    for weights, biases in zip(hidden_weights, hidden_biases):
        next_values: list[float] = []
        for row, bias in zip(weights, biases):
            value = float(bias) + sum(float(weight) * item for weight, item in zip(row, activations))
            next_values.append(max(0.0, value))
        activations = next_values
    logits = [
        float(bias) + sum(float(weight) * item for weight, item in zip(row, activations))
        for row, bias in zip(output_weights, output_bias)
    ]
    return _normalized_exponential_weights(logits)


def graph_nodes(row: dict[str, Any], *, include_blank: bool = False) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    glyph_nodes = row.get("glyph_nodes", []) or []
    for index, item in enumerate(glyph_nodes):
        if isinstance(item, dict):
            node = _normalize_node(item, index=index, node_type="glyph")
            if include_blank or str(node.get("text", "")).strip():
                nodes.append(node)
    offset = len(glyph_nodes)
    for index, item in enumerate(row.get("vector_nodes", []) or []):
        if isinstance(item, dict):
            nodes.append(_normalize_node(item, index=offset + index, node_type="vector"))
    return sorted(nodes, key=lambda item: (item["bbox"][0], item["bbox"][1], item["node_id"]))


def candidate_pairs(nodes: list[dict[str, Any]], *, max_neighbors: int = 16) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    ordered = sorted(nodes, key=lambda item: (item["bbox"][0], item["bbox"][1], item["node_id"]))
    for source_index, source in enumerate(ordered):
        if source.get("node_type") == "vector":
            pairs.append((source, source))
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
        "source_is_horizontal_rule": 1.0 if _is_horizontal_rule_node(source) else 0.0,
        "source_is_vertical_rule": 1.0 if _is_vertical_rule_node(source) else 0.0,
        "target_is_horizontal_rule": 1.0 if _is_horizontal_rule_node(target) else 0.0,
        "target_is_vertical_rule": 1.0 if _is_vertical_rule_node(target) else 0.0,
        "source_aspect_ratio": min(1000.0, sw / max(sh, 1e-6)),
        "target_aspect_ratio": min(1000.0, tw / max(th, 1e-6)),
        "same_font": 1.0 if str(source.get("font", "")) == str(target.get("font", "")) else 0.0,
        "source_index": source_index / max(1.0, len(nodes) - 1),
        "target_index": target_index / max(1.0, len(nodes) - 1),
    }


def graph_parser_node_features(node: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, float]:
    x0, y0, x1, y1 = node["bbox"]
    width = max(0.0, float(x1) - float(x0))
    height = max(0.0, float(y1) - float(y0))
    area = width * height
    span = _nodes_bbox(nodes)
    span_width = max(1e-6, span[2] - span[0])
    span_height = max(1e-6, span[3] - span[1])
    text = str(node.get("text", "") or "")
    flags = _unicode_category_flags(text)
    font_size = float(node.get("size", 0.0) or 0.0)
    sizes = [float(item.get("size", 0.0) or 0.0) for item in nodes if float(item.get("size", 0.0) or 0.0) > 0]
    mean_size = sum(sizes) / len(sizes) if sizes else 0.0
    order_index = float(node.get("order_index", 0))
    return {
        "bias": 1.0,
        "width": width,
        "height": height,
        "area": area,
        "aspect_ratio": min(1000.0, width / max(height, 1e-6)),
        "relative_width": width / span_width,
        "relative_height": height / span_height,
        "x_span_ratio": _overlap(x0, x1, span[0], span[2]) / span_width,
        "y_span_ratio": _overlap(y0, y1, span[1], span[3]) / span_height,
        "center_x": float(node.get("center", [0.0, 0.0])[0]),
        "center_y": float(node.get("center", [0.0, 0.0])[1]),
        "text_length": float(len(text)),
        "text_is_blank": 1.0 if not text.strip() else 0.0,
        "text_is_single_char": 1.0 if len(text) == 1 else 0.0,
        "text_is_ascii": 1.0 if text and all(ord(char) < 128 for char in text) else 0.0,
        "text_has_letter": flags["letter"],
        "text_has_number": flags["number"],
        "text_has_symbol": flags["symbol"],
        "text_has_punctuation": flags["punctuation"],
        "text_has_separator": flags["separator"],
        "text_has_mark": flags["mark"],
        "is_math": 1.0 if node.get("is_math_font") else 0.0,
        "is_rule": 1.0 if node.get("node_type") == "vector" else 0.0,
        "is_horizontal_rule": 1.0 if _is_horizontal_rule_node(node) else 0.0,
        "is_vertical_rule": 1.0 if _is_vertical_rule_node(node) else 0.0,
        "rule_aspect_ratio": min(1000.0, width / max(height, 1e-6)) if node.get("node_type") == "vector" else 0.0,
        "font_size": font_size,
        "font_size_ratio": font_size / max(mean_size, 1e-6) if font_size > 0 and mean_size > 0 else 0.0,
        "order_index": order_index / max(1.0, len(nodes) - 1),
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
        for label in _structure_relation_labels_from_structure(nodes, alignment):
            relation = str(label.get("relation", "") or "")
            if relation not in GRAPH_PARSER_RELATIONS or relation == "NONE":
                continue
            source = str(label.get("source", "") or "")
            target = str(label.get("target", "") or "")
            confidence = float(label.get("confidence", 0.0) or 0.0)
            current = positive.get((source, target))
            if current is None or confidence >= current[1]:
                positive[(source, target)] = (relation, confidence)
        pairs = candidate_pairs(nodes)
        node_by_id = {str(node.get("node_id", "") or ""): node for node in nodes}
        pair_keys = {(str(source["node_id"]), str(target["node_id"])) for source, target in pairs}
        for source_id, target_id in positive:
            if (source_id, target_id) in pair_keys:
                continue
            if source_id not in node_by_id or target_id not in node_by_id:
                continue
            pairs.append((node_by_id[source_id], node_by_id[target_id]))
            pair_keys.add((source_id, target_id))
        for source, target in pairs:
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


def node_training_samples_from_rows(
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
        aligned: dict[str, dict[str, Any]] = {}
        for item in alignment.get("node_alignments", []) or []:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("pdf_node_id", "") or "")
            if not node_id:
                continue
            confidence = float(item.get("confidence", 0.0) or 0.0)
            current = aligned.get(node_id)
            if current is None or confidence > float(current.get("confidence", 0.0) or 0.0):
                aligned[node_id] = item
        ignored: dict[str, str] = {}
        for item in alignment.get("ignored_pdf_nodes", []) or []:
            if isinstance(item, dict):
                node_id = str(item.get("pdf_node_id", "") or "")
                if node_id:
                    ignored[node_id] = str(item.get("reason", "") or "")
        nodes = graph_nodes(row, include_blank=True)
        for node in nodes:
            node_id = str(node.get("node_id", "") or "")
            label = "UNKNOWN"
            confidence = 0.25
            if node.get("node_type") == "vector":
                label, confidence = _generic_rule_node_label(node)
            elif node_id in aligned:
                label = _node_label_from_alignment(aligned[node_id])
                confidence = float(aligned[node_id].get("confidence", 0.0) or 0.0)
            elif ignored.get(node_id) == "spacing_or_blank":
                label = "SPACING"
                confidence = 1.0
            samples.append(
                {
                    "row_id": row_id,
                    "node_id": node_id,
                    "label": label,
                    "confidence": confidence,
                    "features": graph_parser_node_features(node, nodes),
                }
            )
    return samples


def _node_label_from_alignment(alignment: dict[str, Any]) -> str:
    target_node_type = str(alignment.get("target_node_type", "") or "")
    attrs = alignment.get("target_attrs", {})
    if not isinstance(attrs, dict):
        attrs = {}
    katex_type = str(attrs.get("katex_type", "") or "")
    family = str(attrs.get("family", "") or "")
    if target_node_type == "text_run":
        if bool(attrs.get("operator")):
            return "OPERATOR"
        return "TEXT"
    if target_node_type == "equation_number":
        return "EQUATION_TAG"
    if target_node_type == "symbol" and (
        katex_type in GRAPH_PARSER_OPERATOR_KATEX_TYPES or family in GRAPH_PARSER_OPERATOR_KATEX_TYPES
    ):
        return "OPERATOR"
    return "SYMBOL"


def _generic_rule_node_label(node: dict[str, Any]) -> tuple[str, float]:
    x0, y0, x1, y1 = node.get("bbox", [0.0, 0.0, 0.0, 0.0])
    width = max(0.0, float(x1) - float(x0))
    height = max(0.0, float(y1) - float(y0))
    if width <= 0.0 and height <= 0.0:
        return "UNKNOWN", 0.25
    if _is_horizontal_rule_node(node):
        return "HORIZONTAL_RULE", 1.0
    if _is_vertical_rule_node(node):
        return "VERTICAL_RULE", 1.0
    return ("HORIZONTAL_RULE", 0.75) if width >= height else ("VERTICAL_RULE", 0.75)


def _is_horizontal_rule_node(node: dict[str, Any]) -> bool:
    if node.get("node_type") != "vector":
        return False
    if bool(node.get("is_horizontal_rule_candidate")):
        return True
    x0, y0, x1, y1 = node.get("bbox", [0.0, 0.0, 0.0, 0.0])
    width = max(0.0, float(x1) - float(x0))
    height = max(0.0, float(y1) - float(y0))
    return width / max(height, 1e-6) >= 6.0 and height <= max(width * 0.08, 2.0)


def _is_vertical_rule_node(node: dict[str, Any]) -> bool:
    if node.get("node_type") != "vector":
        return False
    if bool(node.get("is_vertical_rule_candidate")):
        return True
    x0, y0, x1, y1 = node.get("bbox", [0.0, 0.0, 0.0, 0.0])
    width = max(0.0, float(x1) - float(x0))
    height = max(0.0, float(y1) - float(y0))
    return height / max(width, 1e-6) >= 6.0 and width <= max(height * 0.08, 2.0)


def _structure_relation_labels_from_structure(
    nodes: list[dict[str, Any]],
    alignment: dict[str, Any],
) -> list[dict[str, Any]]:
    vectors = [node for node in nodes if node.get("node_type") == "vector"]
    node_by_id = {str(node.get("node_id", "") or ""): node for node in nodes}
    output: list[dict[str, Any]] = []
    output.extend(_matrix_grid_relations_from_structure(node_by_id, alignment.get("structure_labels", []) or []))
    for item in alignment.get("structure_labels", []) or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "")
        if role in {"TARGET_TEXT_RUN_EVIDENCE", "TARGET_OPERATOR_TEXT_RUN_EVIDENCE"}:
            text_nodes = _nodes_for_ids(node_by_id, item.get("text_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for previous, current in zip(text_nodes, text_nodes[1:]):
                output.append(
                    _training_relation(
                        str(previous.get("node_id", "") or ""),
                        str(current.get("node_id", "") or ""),
                        "TEXT_RUN_NEXT",
                        confidence,
                    )
                )
        elif role == "TARGET_RADICAL_MARK_EVIDENCE":
            mark_nodes = _nodes_for_ids(node_by_id, item.get("mark_pdf_node_ids", []))
            body_nodes = _nodes_for_ids(node_by_id, item.get("body_pdf_node_ids", []))
            index_nodes = _nodes_for_ids(node_by_id, item.get("index_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for mark in mark_nodes:
                for body in body_nodes:
                    output.append(
                        _training_relation(
                            str(mark.get("node_id", "") or ""),
                            str(body.get("node_id", "") or ""),
                            "RADICAL_BODY",
                            confidence,
                        )
                    )
                for index in index_nodes:
                    output.append(
                        _training_relation(
                            str(mark.get("node_id", "") or ""),
                            str(index.get("node_id", "") or ""),
                            "RADICAL_INDEX",
                            confidence,
                        )
                    )
        elif role == "TARGET_UNDER_OVER_EVIDENCE":
            base_nodes = _nodes_for_ids(node_by_id, item.get("base_pdf_node_ids", []))
            under_nodes = _nodes_for_ids(node_by_id, item.get("under_pdf_node_ids", []))
            over_nodes = _nodes_for_ids(node_by_id, item.get("over_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for base in base_nodes[:1]:
                for under in under_nodes:
                    output.append(
                        _training_relation(
                            str(base.get("node_id", "") or ""),
                            str(under.get("node_id", "") or ""),
                            "UNDER",
                            confidence,
                        )
                    )
                for over in over_nodes:
                    output.append(
                        _training_relation(
                            str(base.get("node_id", "") or ""),
                            str(over.get("node_id", "") or ""),
                            "OVER",
                            confidence,
                        )
                    )
        elif role == "TARGET_LEFT_ATTACHMENT_EVIDENCE":
            base_nodes = _nodes_for_ids(node_by_id, item.get("base_pdf_node_ids", []))
            pre_sub_nodes = _nodes_for_ids(node_by_id, item.get("pre_sub_pdf_node_ids", []))
            pre_sup_nodes = _nodes_for_ids(node_by_id, item.get("pre_sup_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for base in base_nodes[:1]:
                for pre_sub in pre_sub_nodes:
                    output.append(
                        _training_relation(
                            str(base.get("node_id", "") or ""),
                            str(pre_sub.get("node_id", "") or ""),
                            "PRE_SUB",
                            confidence,
                        )
                    )
                for pre_sup in pre_sup_nodes:
                    output.append(
                        _training_relation(
                            str(base.get("node_id", "") or ""),
                            str(pre_sup.get("node_id", "") or ""),
                            "PRE_SUP",
                            confidence,
                        )
                    )
        elif role == "TARGET_FENCE_EVIDENCE":
            body_nodes = _nodes_for_ids(node_by_id, item.get("body_pdf_node_ids", []))
            open_nodes = _nodes_for_ids(node_by_id, item.get("open_pdf_node_ids", []))
            close_nodes = _nodes_for_ids(node_by_id, item.get("close_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for body_anchor in body_nodes[:1]:
                for open_node in open_nodes:
                    output.append(
                        _training_relation(
                            str(body_anchor.get("node_id", "") or ""),
                            str(open_node.get("node_id", "") or ""),
                            "FENCE_OPEN",
                            confidence,
                        )
                    )
                    output.append(
                        _training_relation(
                            str(open_node.get("node_id", "") or ""),
                            str(body_anchor.get("node_id", "") or ""),
                            "FENCE_BODY",
                            confidence,
                        )
                    )
                for close_node in close_nodes:
                    output.append(
                        _training_relation(
                            str(body_anchor.get("node_id", "") or ""),
                            str(close_node.get("node_id", "") or ""),
                            "FENCE_CLOSE",
                            confidence,
                        )
                    )
                    output.append(
                        _training_relation(
                            str(close_node.get("node_id", "") or ""),
                            str(body_anchor.get("node_id", "") or ""),
                            "FENCE_BODY",
                            confidence,
                        )
                    )
        elif role == "TARGET_MATRIX_CELL_EVIDENCE":
            cell_nodes = _nodes_for_ids(node_by_id, item.get("cell_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for cell_anchor in cell_nodes[:1]:
                for cell_member in cell_nodes[1:]:
                    output.append(
                        _training_relation(
                            str(cell_anchor.get("node_id", "") or ""),
                            str(cell_member.get("node_id", "") or ""),
                            "CELL_CONTENT",
                            confidence,
                        )
                    )
        elif role == "TARGET_ENCLOSURE_EVIDENCE":
            if not vectors:
                continue
            body_nodes = _nodes_for_ids(node_by_id, item.get("body_pdf_node_ids", []))
            if not body_nodes:
                continue
            scored = [(_enclosure_rule_evidence_score(vector, body_nodes), vector) for vector in vectors]
            score, vector = max(
                scored,
                key=lambda pair: (pair[0], str(pair[1].get("node_id", "") or "")),
                default=(0.0, {}),
            )
            if score < 0.35:
                continue
            vector_id = str(vector.get("node_id", "") or "")
            confidence = round(score * float(item.get("confidence", 1.0) or 1.0), 6)
            for body_node in body_nodes:
                output.append(
                    _training_relation(
                        vector_id,
                        str(body_node.get("node_id", "") or ""),
                        "ENCLOSURE_BODY",
                        confidence,
                    )
                )
        elif role == "TARGET_EQUATION_TAG_EVIDENCE":
            tag_nodes = _nodes_for_ids(node_by_id, item.get("tag_pdf_node_ids", []))
            if not tag_nodes:
                continue
            anchor = _equation_tag_anchor_node(node_by_id, tag_nodes)
            if not anchor:
                continue
            confidence = float(item.get("confidence", 1.0) or 1.0)
            anchor_id = str(anchor.get("node_id", "") or "")
            for tag_node in tag_nodes:
                output.append(
                    _training_relation(
                        anchor_id,
                        str(tag_node.get("node_id", "") or ""),
                        "EQUATION_TAG",
                        confidence,
                    )
                )
        elif role == "TARGET_FRACTION_SEPARATOR_EVIDENCE":
            if not vectors:
                continue
            above_nodes = _nodes_for_ids(node_by_id, item.get("above_pdf_node_ids", []))
            below_nodes = _nodes_for_ids(node_by_id, item.get("below_pdf_node_ids", []))
            scored = [(_fraction_separator_evidence_score(vector, above_nodes, below_nodes), vector) for vector in vectors]
            score, vector = max(
                scored,
                key=lambda pair: (pair[0], str(pair[1].get("node_id", "") or "")),
                default=(0.0, {}),
            )
            if score < 0.45:
                continue
            vector_id = str(vector.get("node_id", "") or "")
            confidence = round(score * float(item.get("confidence", 1.0) or 1.0), 6)
            output.append(_training_relation(vector_id, vector_id, "FRACTION_BAR", confidence))
            for node in above_nodes:
                output.append(_training_relation(vector_id, str(node.get("node_id", "") or ""), "ABOVE", confidence))
            for node in below_nodes:
                output.append(_training_relation(vector_id, str(node.get("node_id", "") or ""), "BELOW", confidence))
        elif role == "TARGET_ACCENT_ANNOTATION_EVIDENCE":
            if not vectors:
                continue
            base_nodes = _nodes_for_ids(node_by_id, item.get("base_pdf_node_ids", []))
            position = str(item.get("annotation_position", "") or "")
            relation = {"over": "OVERLINE", "under": "UNDERLINE"}.get(position)
            if relation is None:
                continue
            scored = [(_annotation_rule_evidence_score(vector, base_nodes, position=position), vector) for vector in vectors]
            score, vector = max(
                scored,
                key=lambda pair: (pair[0], str(pair[1].get("node_id", "") or "")),
                default=(0.0, {}),
            )
            if score < 0.45:
                continue
            vector_id = str(vector.get("node_id", "") or "")
            confidence = round(score * float(item.get("confidence", 1.0) or 1.0), 6)
            output.append(_training_relation(vector_id, vector_id, relation, confidence))
            child_relation = "BELOW" if position == "over" else "ABOVE"
            for node in base_nodes:
                output.append(_training_relation(vector_id, str(node.get("node_id", "") or ""), child_relation, confidence))
    return output


def _equation_tag_anchor_node(
    node_by_id: dict[str, dict[str, Any]],
    tag_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    tag_ids = {str(node.get("node_id", "") or "") for node in tag_nodes}
    body_nodes = [
        node
        for node in node_by_id.values()
        if str(node.get("node_id", "") or "") not in tag_ids
        and node.get("node_type") == "glyph"
        and str(node.get("text", "") or node.get("unicode", "") or node.get("latex", "") or "").strip()
    ]
    if not body_nodes:
        return {}
    tag_bbox = _nodes_bbox(tag_nodes)
    tag_center_y = (tag_bbox[1] + tag_bbox[3]) / 2.0
    left_of_tag = [node for node in body_nodes if float(node.get("center", [0.0, 0.0])[0]) <= tag_bbox[0]]
    candidates = left_of_tag or body_nodes
    return min(
        candidates,
        key=lambda node: (
            abs(float(node.get("center", [0.0, 0.0])[1]) - tag_center_y),
            abs(float(node.get("center", [0.0, 0.0])[0]) - tag_bbox[0]),
            str(node.get("node_id", "") or ""),
        ),
    )


def _matrix_grid_relations_from_structure(
    node_by_id: dict[str, dict[str, Any]],
    structure_labels: list[Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cells_by_row: dict[int, list[dict[str, Any]]] = {}
    output: list[dict[str, Any]] = []
    for item in structure_labels:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "")
        if role == "TARGET_MATRIX_ROW_EVIDENCE":
            row_nodes = _nodes_for_ids(node_by_id, item.get("row_pdf_node_ids", []))
            if row_nodes:
                rows.append(
                    {
                        "row_index": _int(item.get("row_index")),
                        "anchor": row_nodes[0],
                        "confidence": float(item.get("confidence", 1.0) or 1.0),
                    }
                )
        elif role == "TARGET_MATRIX_CELL_EVIDENCE":
            cell_nodes = _nodes_for_ids(node_by_id, item.get("cell_pdf_node_ids", []))
            if cell_nodes:
                row_index = _int(item.get("row_index"))
                cells_by_row.setdefault(row_index, []).append(
                    {
                        "column_index": _int(item.get("column_index")),
                        "anchor": cell_nodes[0],
                        "confidence": float(item.get("confidence", 1.0) or 1.0),
                    }
                )
    ordered_rows = sorted(rows, key=lambda item: (int(item["row_index"]), str(item["anchor"].get("node_id", "") or "")))
    for previous, current in zip(ordered_rows, ordered_rows[1:]):
        output.append(
            _training_relation(
                str(previous["anchor"].get("node_id", "") or ""),
                str(current["anchor"].get("node_id", "") or ""),
                "MATRIX_ROW",
                min(float(previous["confidence"]), float(current["confidence"])),
            )
        )
    for cells in cells_by_row.values():
        ordered_cells = sorted(cells, key=lambda item: (int(item["column_index"]), str(item["anchor"].get("node_id", "") or "")))
        for previous, current in zip(ordered_cells, ordered_cells[1:]):
            output.append(
                _training_relation(
                    str(previous["anchor"].get("node_id", "") or ""),
                    str(current["anchor"].get("node_id", "") or ""),
                    "MATRIX_CELL",
                    min(float(previous["confidence"]), float(current["confidence"])),
                )
            )
    return output


def _training_relation(source: str, target: str, relation: str, confidence: float) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "confidence": confidence,
    }


def _nodes_for_ids(node_by_id: dict[str, dict[str, Any]], node_ids: Any) -> list[dict[str, Any]]:
    if not isinstance(node_ids, (list, tuple, set)):
        return []
    return [
        node_by_id[node_id]
        for node_id in (str(item) for item in node_ids)
        if node_id in node_by_id
    ]


def _fraction_separator_evidence_score(
    vector: dict[str, Any],
    above_nodes: list[dict[str, Any]],
    below_nodes: list[dict[str, Any]],
) -> float:
    if vector.get("node_type") != "vector" or not above_nodes or not below_nodes:
        return 0.0
    vx0, vy0, vx1, vy1 = vector["bbox"]
    v_width = max(0.0, vx1 - vx0)
    v_height = max(0.0, vy1 - vy0)
    above_bbox = _nodes_bbox(above_nodes)
    below_bbox = _nodes_bbox(below_nodes)
    above_center_y = _bbox_center_y(above_bbox)
    below_center_y = _bbox_center_y(below_bbox)
    if above_center_y >= below_center_y:
        return 0.0
    combined_bbox = _merge_bbox(above_bbox, below_bbox)
    vertical_gap = max(1e-6, below_center_y - above_center_y)
    vector_center_y = (vy0 + vy1) / 2.0
    between = 1.0 - min(1.0, abs(vector_center_y - ((above_center_y + below_center_y) / 2.0)) / vertical_gap)
    if above_center_y <= vector_center_y <= below_center_y:
        between = max(between, 0.85)
    span = _overlap(vx0, vx1, combined_bbox[0], combined_bbox[2]) / max(1e-6, combined_bbox[2] - combined_bbox[0])
    shape = _horizontal_rule_score(v_width, v_height)
    return round(max(0.0, min(1.0, (0.35 * shape) + (0.35 * between) + (0.30 * span))), 6)


def _annotation_rule_evidence_score(
    vector: dict[str, Any],
    base_nodes: list[dict[str, Any]],
    *,
    position: str,
) -> float:
    if vector.get("node_type") != "vector" or not base_nodes:
        return 0.0
    vx0, vy0, vx1, vy1 = vector["bbox"]
    v_width = max(0.0, vx1 - vx0)
    v_height = max(0.0, vy1 - vy0)
    base_bbox = _nodes_bbox(base_nodes)
    vector_center_y = (vy0 + vy1) / 2.0
    base_center_y = _bbox_center_y(base_bbox)
    if position == "over" and vector_center_y > base_center_y:
        return 0.0
    if position == "under" and vector_center_y < base_center_y:
        return 0.0
    vertical_distance = abs(vector_center_y - base_center_y)
    base_height = max(1e-6, base_bbox[3] - base_bbox[1])
    proximity = 1.0 - min(1.0, vertical_distance / max(base_height * 2.0, 1e-6))
    span = _overlap(vx0, vx1, base_bbox[0], base_bbox[2]) / max(1e-6, base_bbox[2] - base_bbox[0])
    shape = _horizontal_rule_score(v_width, v_height)
    return round(max(0.0, min(1.0, (0.40 * shape) + (0.35 * span) + (0.25 * proximity))), 6)


def _enclosure_rule_evidence_score(
    vector: dict[str, Any],
    body_nodes: list[dict[str, Any]],
) -> float:
    if vector.get("node_type") != "vector" or not body_nodes:
        return 0.0
    vx0, vy0, vx1, vy1 = vector["bbox"]
    v_width = max(0.0, vx1 - vx0)
    v_height = max(0.0, vy1 - vy0)
    body_bbox = _nodes_bbox(body_nodes)
    body_width = max(1e-6, body_bbox[2] - body_bbox[0])
    body_height = max(1e-6, body_bbox[3] - body_bbox[1])
    horizontal_shape = _horizontal_rule_score(v_width, v_height)
    vertical_shape = _vertical_rule_score(v_width, v_height)
    if horizontal_shape >= vertical_shape:
        span = _overlap(vx0, vx1, body_bbox[0], body_bbox[2]) / body_width
        distance = min(
            abs(vy0 - body_bbox[1]),
            abs(vy0 - body_bbox[3]),
            abs(vy1 - body_bbox[1]),
            abs(vy1 - body_bbox[3]),
        )
        proximity = 1.0 - min(1.0, distance / max(body_height * 1.5, 1e-6))
        shape = horizontal_shape
    else:
        span = _overlap(vy0, vy1, body_bbox[1], body_bbox[3]) / body_height
        distance = min(
            abs(vx0 - body_bbox[0]),
            abs(vx0 - body_bbox[2]),
            abs(vx1 - body_bbox[0]),
            abs(vx1 - body_bbox[2]),
        )
        proximity = 1.0 - min(1.0, distance / max(body_width * 1.5, 1e-6))
        shape = vertical_shape
    return round(max(0.0, min(1.0, (0.45 * shape) + (0.35 * span) + (0.20 * proximity))), 6)


def _unicode_category_flags(text: str) -> dict[str, float]:
    categories = [unicodedata.category(char) for char in str(text or "")]
    return {
        "letter": 1.0 if any(category.startswith("L") for category in categories) else 0.0,
        "number": 1.0 if any(category.startswith("N") for category in categories) else 0.0,
        "symbol": 1.0 if any(category.startswith("S") for category in categories) else 0.0,
        "punctuation": 1.0 if any(category.startswith("P") for category in categories) else 0.0,
        "separator": 1.0 if any(category.startswith("Z") for category in categories) else 0.0,
        "mark": 1.0 if any(category.startswith("M") for category in categories) else 0.0,
    }


def graph_parser_predictions_to_structural_candidate(predictions: dict[str, Any]) -> dict[str, Any]:
    relation_map = {
        "NEXT": "HORIZONTAL",
        "SUB": "SUB",
        "SUP": "SUP",
        "PRE_SUB": "PRE_SUB",
        "PRE_SUP": "PRE_SUP",
        "UNDER": "UNDER",
        "OVER": "OVER",
        "ACCENT_BASE": "ACCENT_BASE",
        "RADICAL_BODY": "RADICAL_BODY",
        "RADICAL_INDEX": "RADICAL_INDEX",
        "NUMERATOR": "ABOVE",
        "DENOMINATOR": "BELOW",
        "FRACTION_BAR": "FRACTION_BAR",
        "OVERLINE": "OVERLINE",
        "UNDERLINE": "UNDERLINE",
        "ABOVE": "ABOVE",
        "BELOW": "BELOW",
        "FENCE_BODY": "FENCE_BODY",
        "FENCE_OPEN": "FENCE_OPEN",
        "FENCE_CLOSE": "FENCE_CLOSE",
        "MATRIX_ROW": "MATRIX_ROW",
        "MATRIX_CELL": "MATRIX_CELL",
        "CELL_CONTENT": "CELL_CONTENT",
        "TEXT_RUN_NEXT": "TEXT_RUN_NEXT",
        "ENCLOSURE_BODY": "ENCLOSURE_BODY",
        "EQUATION_TAG": "EQUATION_TAG",
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
    selected = _dedupe_selected_relations(selected)
    relation_alternatives = _map_relation_alternatives(
        predictions.get("relation_alternatives", []) or [],
        relation_map,
    )
    warnings: list[str] = []
    if not selected:
        warnings.append("graph_parser_no_selected_relations")
    return {
        "candidate_only": True,
        "abstain": not bool(selected),
        "selected_relations": selected,
        "relation_alternatives": relation_alternatives,
        "node_predictions": list(predictions.get("node_predictions", []) or []),
        "node_filter_threshold": float(
            predictions.get("node_filter_threshold", GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD)
            or GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD
        ),
        "verifier_warnings": sorted(set(warnings)),
        "model_version": str(predictions.get("model_version", "") or ""),
    }


def _top_relation_alternatives(
    probabilities: list[float],
    labels: tuple[str, ...],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    alternatives: list[dict[str, Any]] = []
    for index, probability in sorted(
        enumerate(probabilities[: len(labels)]),
        key=lambda item: float(item[1]),
        reverse=True,
    ):
        label = str(labels[index])
        if label == "NONE":
            continue
        alternatives.append({"relation": label, "confidence": round(float(probability), 6)})
        if len(alternatives) >= limit:
            break
    return alternatives


def _map_relation_alternatives(
    items: list[Any],
    relation_map: dict[str, str],
) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        alternatives: list[dict[str, Any]] = []
        for alternative in item.get("alternatives", []) or []:
            if not isinstance(alternative, dict):
                continue
            relation = relation_map.get(str(alternative.get("relation", "") or ""))
            if relation is None:
                continue
            alternatives.append(
                {
                    "relation": relation,
                    "confidence": float(alternative.get("confidence", 0.0) or 0.0),
                }
            )
        if alternatives:
            mapped.append(
                {
                    "source": str(item.get("source", "") or ""),
                    "target": str(item.get("target", "") or ""),
                    "alternatives": alternatives,
                }
            )
    return sorted(mapped, key=lambda item: (item["source"], item["target"]))


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
        "size": float(item.get("size", 0.0) or 0.0),
        "bbox": bbox_values,
        "center": [
            (bbox_values[0] + bbox_values[2]) / 2.0,
            (bbox_values[1] + bbox_values[3]) / 2.0,
        ],
        "is_math_font": bool(item.get("is_math_font", False)),
        "order_index": index,
    }


def _dedupe_selected_relations(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in relations:
        key = (
            str(item.get("source", "") or ""),
            str(item.get("target", "") or ""),
            str(item.get("relation", "") or ""),
        )
        current = best.get(key)
        if current is None or float(item.get("confidence", 0.0) or 0.0) > float(current.get("confidence", 0.0) or 0.0):
            best[key] = item
    output = []
    for index, item in enumerate(sorted(best.values(), key=lambda value: (value["source"], value["target"], value["relation"]))):
        payload = dict(item)
        payload["edge_id"] = str(payload.get("edge_id", "") or f"gp{index:05d}")
        output.append(payload)
    return output


def _nodes_bbox(nodes: list[dict[str, Any]]) -> list[float]:
    boxes = [
        node.get("bbox", [0.0, 0.0, 0.0, 0.0])
        for node in nodes
        if isinstance(node.get("bbox", None), (list, tuple)) and len(node.get("bbox", [])) == 4
    ]
    if not boxes:
        return [0.0, 0.0, 1.0, 1.0]
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _merge_bbox(first: list[float], second: list[float]) -> list[float]:
    return [
        min(float(first[0]), float(second[0])),
        min(float(first[1]), float(second[1])),
        max(float(first[2]), float(second[2])),
        max(float(first[3]), float(second[3])),
    ]


def _bbox_center_y(bbox: list[float]) -> float:
    return (float(bbox[1]) + float(bbox[3])) / 2.0


def _horizontal_rule_score(width: float, height: float) -> float:
    if width <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (height / max(width, 1e-6))))


def _vertical_rule_score(width: float, height: float) -> float:
    if height <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (width / max(height, 1e-6))))


def _node_sort_key(node: dict[str, Any]) -> tuple[float, float, str]:
    bbox = node.get("bbox", [0.0, 0.0, 0.0, 0.0])
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        bbox = [0.0, 0.0, 0.0, 0.0]
    return (float(bbox[0]), float(bbox[1]), str(node.get("node_id", "") or ""))


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _normalized_exponential_weights(logits: list[float]) -> list[float]:
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
