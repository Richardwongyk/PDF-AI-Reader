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


GRAPH_PARSER_ARTIFACT_VERSION = "tinybdmath_graph_parser_m5_json_v1"
GRAPH_PARSER_FEATURE_VERSION = "tinybdmath_graph_parser_features_v9"

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
GRAPH_PARSER_NON_RUNTIME_SUPERVISION_RELATIONS = {
    "BASE",
    "CHILD",
}
GRAPH_PARSER_CHAIN_RELATIONS = {
    "NEXT",
    "TEXT_RUN_NEXT",
    "MATRIX_ROW",
    "MATRIX_CELL",
}
GRAPH_PARSER_EXCLUSIVE_CHILD_RELATIONS = {
    "SUB",
    "SUP",
    "PRE_SUB",
    "PRE_SUP",
    "UNDER",
    "OVER",
    "ACCENT_BASE",
    "RADICAL_BODY",
    "RADICAL_INDEX",
    "FENCE_BODY",
    "FENCE_OPEN",
    "FENCE_CLOSE",
    "CELL_CONTENT",
    "EQUATION_TAG",
}

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
GRAPH_PARSER_RUNTIME_RELATION_CONFIDENCE_FLOOR = 0.85
GRAPH_PARSER_ALTERNATIVE_CONFIDENCE_FLOOR = 0.20
GRAPH_PARSER_ALTERNATIVE_THRESHOLD_FACTOR = 0.50
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
    "prev_same_font",
    "next_same_font",
    "prev_baseline_alignment",
    "next_baseline_alignment",
    "prev_horizontal_gap",
    "next_horizontal_gap",
    "letter_run_left",
    "letter_run_right",
    "letter_run_length",
    "same_font_letter_run_length",
)

GRAPH_PARSER_GRAPH_FEATURES = (
    "bias",
    "node_count",
    "glyph_count",
    "vector_count",
    "candidate_pair_count",
    "graph_width",
    "graph_height",
    "graph_aspect_ratio",
    "candidate_density",
    "source_out_degree",
    "source_in_degree",
    "target_out_degree",
    "target_in_degree",
    "source_out_degree_ratio",
    "source_in_degree_ratio",
    "target_out_degree_ratio",
    "target_in_degree_ratio",
    "candidate_distance_rank",
    "candidate_distance_rank_ratio",
    "candidate_distance_percentile",
    "source_min_distance",
    "source_mean_distance",
    "target_min_distance",
    "target_mean_distance",
    "source_rule_neighbor_count",
    "target_rule_neighbor_count",
)


@dataclass(frozen=True)
class TinyBDGraphParserPrediction:
    source: str
    target: str
    relation: str
    confidence: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def graph_parser_structured_relation_selection(predictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used_chain_sources: set[tuple[str, str]] = set()
    used_chain_targets: set[tuple[str, str]] = set()
    used_child_targets: set[str] = set()
    ordered = sorted(
        (item for item in predictions if isinstance(item, dict)),
        key=lambda item: (
            -float(item.get("confidence", 0.0) or 0.0),
            str(item.get("source", "") or ""),
            str(item.get("target", "") or ""),
            str(item.get("relation", "") or ""),
        ),
    )
    for item in ordered:
        source = str(item.get("source", "") or "")
        target = str(item.get("target", "") or "")
        relation = str(item.get("relation", "") or "")
        if not source or not target or not relation or relation == "NONE":
            continue
        if relation in GRAPH_PARSER_CHAIN_RELATIONS:
            source_key = (relation, source)
            target_key = (relation, target)
            if source_key in used_chain_sources or target_key in used_chain_targets:
                continue
            used_chain_sources.add(source_key)
            used_chain_targets.add(target_key)
        elif relation in GRAPH_PARSER_EXCLUSIVE_CHILD_RELATIONS:
            if target in used_child_targets:
                continue
            used_child_targets.add(target)
        selected.append(dict(item))
    return sorted(selected, key=lambda item: (str(item.get("source", "")), str(item.get("target", "")), str(item.get("relation", ""))))


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
    keep_output_weights: tuple[float, ...] = ()
    keep_output_bias: float = 0.0
    keep_threshold: float = 0.5
    graph_feature_names: tuple[str, ...] = GRAPH_PARSER_GRAPH_FEATURES
    graph_means: tuple[float, ...] = ()
    graph_scales: tuple[float, ...] = ()
    graph_hidden_weights: tuple[tuple[tuple[float, ...], ...], ...] = field(default_factory=tuple)
    graph_hidden_biases: tuple[tuple[float, ...], ...] = field(default_factory=tuple)

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
            keep_output_weights=tuple(float(value) for value in payload.get("keep_output_weights", [])),
            keep_output_bias=float(payload.get("keep_output_bias", 0.0) or 0.0),
            keep_threshold=float(payload.get("keep_threshold", 0.5) or 0.5),
            graph_feature_names=tuple(str(item) for item in payload.get("graph_feature_names", GRAPH_PARSER_GRAPH_FEATURES)),
            graph_means=tuple(float(item) for item in payload.get("graph_means", [])),
            graph_scales=tuple(float(item) for item in payload.get("graph_scales", [])),
            graph_hidden_weights=tuple(
                tuple(tuple(float(value) for value in row) for row in layer)
                for layer in payload.get("graph_hidden_weights", [])
            ),
            graph_hidden_biases=tuple(
                tuple(float(value) for value in layer)
                for layer in payload.get("graph_hidden_biases", [])
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
        node_features_by_id = {
            str(node.get("node_id", "") or ""): graph_parser_node_features(node, nodes)
            for node in nodes
        }
        graph_features_by_pair = graph_parser_graph_feature_map(nodes, pairs)
        predictions: list[TinyBDGraphParserPrediction] = []
        relation_alternatives: list[dict[str, Any]] = []
        cutoff = (
            max(float(self.artifact.threshold), GRAPH_PARSER_RUNTIME_RELATION_CONFIDENCE_FLOOR)
            if threshold is None
            else float(threshold)
        )
        alternative_cutoff = max(
            GRAPH_PARSER_ALTERNATIVE_CONFIDENCE_FLOOR,
            cutoff * GRAPH_PARSER_ALTERNATIVE_THRESHOLD_FACTOR,
        )
        keep_cutoff = float(self.artifact.keep_threshold or 0.5)
        selected_confidences: list[float] = []
        for source, target in pairs:
            key = (str(source["node_id"]), str(target["node_id"]))
            probabilities, keep_probability = self._predict_relation_outputs(
                source,
                target,
                nodes,
                node_features_by_id.get(key[0], {}),
                node_features_by_id.get(key[1], {}),
                graph_features_by_pair.get(key, {}),
            )
            if not probabilities:
                continue
            relation, type_confidence, combined_confidence = _select_relation_prediction(
                probabilities,
                self.artifact.relation_labels,
                keep_probability=keep_probability,
            )
            alternatives = _top_relation_alternatives(
                probabilities,
                self.artifact.relation_labels,
                keep_probability=keep_probability,
            )
            selected = (
                relation != "NONE"
                and type_confidence >= cutoff
                and (keep_probability is None or keep_probability >= keep_cutoff)
            )
            if alternatives and (selected or float(alternatives[0].get("confidence", 0.0) or 0.0) >= alternative_cutoff):
                relation_alternatives.append(
                    {
                        "source": str(source["node_id"]),
                        "target": str(target["node_id"]),
                        "keep_confidence": round(float(keep_probability), 6) if keep_probability is not None else None,
                        "alternatives": alternatives,
                    }
                )
            if not selected:
                continue
            selected_confidences.append(combined_confidence)
            predictions.append(
                TinyBDGraphParserPrediction(
                    source=str(source["node_id"]),
                    target=str(target["node_id"]),
                    relation=relation,
                    confidence=round(combined_confidence, 6),
                )
            )
        prediction_payloads = graph_parser_structured_relation_selection([item.to_json() for item in predictions])
        selected_confidences = [float(item.get("confidence", 0.0) or 0.0) for item in prediction_payloads]
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
            "keep_threshold": float(self.artifact.keep_threshold),
            "graph_confidence": round(sum(selected_confidences) / len(selected_confidences), 6) if selected_confidences else 0.0,
            "predictions": prediction_payloads,
            "relation_alternatives": sorted(
                relation_alternatives,
                key=_relation_alternative_sort_key,
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

    def _predict_relation_outputs(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        nodes: list[dict[str, Any]],
        source_node_features: dict[str, float],
        target_node_features: dict[str, float],
        graph_features: dict[str, float],
    ) -> tuple[list[float], float | None]:
        features = graph_parser_features(source, target, nodes)
        if not _artifact_uses_context_relation(self.artifact):
            return self._predict_probabilities(features), None
        return _context_relation_outputs(
            features,
            source_node_features,
            target_node_features,
            graph_features,
            artifact=self.artifact,
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


def _relation_alternative_sort_key(item: dict[str, Any]) -> tuple[float, str, str]:
    alternatives = item.get("alternatives", []) if isinstance(item, dict) else []
    top_confidence = 0.0
    if alternatives and isinstance(alternatives[0], dict):
        top_confidence = _safe_float(alternatives[0].get("confidence"))
    return (-top_confidence, str(item.get("source", "")), str(item.get("target", "")))


def _safe_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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
    values = _normalized_feature_values(features, feature_names=feature_names, means=means, scales=scales)
    activations = _hidden_layer_activations(values, hidden_weights=hidden_weights, hidden_biases=hidden_biases)
    logits = _linear_logits(activations, weights=output_weights, bias=output_bias)
    return _normalized_exponential_weights(logits)


def _artifact_uses_context_relation(artifact: TinyBDGraphParserArtifact) -> bool:
    mode = str((artifact.train_config or {}).get("mode", "") or "")
    model_version = str(artifact.model_version or "")
    return (
        mode in {"graph_parser_m2", "graph_parser_m3", "graph_parser_m4", "graph_parser_m5"}
        or model_version.endswith("_m2")
        or model_version.endswith("_m3")
        or model_version.endswith("_m4")
        or model_version.endswith("_m5")
    )


def _context_relation_outputs(
    edge_features: dict[str, float],
    source_node_features: dict[str, float],
    target_node_features: dict[str, float],
    graph_features: dict[str, float] | None,
    *,
    artifact: TinyBDGraphParserArtifact,
) -> tuple[list[float], float | None]:
    if not artifact.output_weights:
        return [], None
    edge_values = _normalized_feature_values(
        edge_features,
        feature_names=artifact.feature_names,
        means=artifact.means,
        scales=artifact.scales,
    )
    edge_context = _hidden_layer_activations(
        edge_values,
        hidden_weights=artifact.hidden_weights,
        hidden_biases=artifact.hidden_biases,
    )
    source_node_values = _normalized_feature_values(
        source_node_features,
        feature_names=artifact.node_feature_names,
        means=artifact.node_means,
        scales=artifact.node_scales,
    )
    target_node_values = _normalized_feature_values(
        target_node_features,
        feature_names=artifact.node_feature_names,
        means=artifact.node_means,
        scales=artifact.node_scales,
    )
    source_context = _hidden_layer_activations(
        source_node_values,
        hidden_weights=artifact.node_hidden_weights,
        hidden_biases=artifact.node_hidden_biases,
    )
    target_context = _hidden_layer_activations(
        target_node_values,
        hidden_weights=artifact.node_hidden_weights,
        hidden_biases=artifact.node_hidden_biases,
    )
    graph_context: list[float] = []
    if _artifact_uses_graph_context_relation(artifact):
        graph_values = _normalized_feature_values(
            graph_features or {},
            feature_names=artifact.graph_feature_names,
            means=artifact.graph_means,
            scales=artifact.graph_scales,
        )
        graph_context = _hidden_layer_activations(
            graph_values,
            hidden_weights=artifact.graph_hidden_weights,
            hidden_biases=artifact.graph_hidden_biases,
        )
    source_logits = _linear_logits(source_context, weights=artifact.node_output_weights, bias=artifact.node_output_bias)
    target_logits = _linear_logits(target_context, weights=artifact.node_output_weights, bias=artifact.node_output_bias)
    fused = _relation_fusion_activations(
        edge_context=edge_context,
        source_context=source_context,
        target_context=target_context,
        graph_context=graph_context,
        source_logits=source_logits,
        target_logits=target_logits,
        artifact=artifact,
    )
    logits = _linear_logits(fused, weights=artifact.output_weights, bias=artifact.output_bias)
    keep_probability = _keep_probability(fused, artifact=artifact)
    return _normalized_exponential_weights(logits), keep_probability


def _relation_fusion_activations(
    *,
    edge_context: list[float],
    source_context: list[float],
    target_context: list[float],
    graph_context: list[float],
    source_logits: list[float],
    target_logits: list[float],
    artifact: TinyBDGraphParserArtifact,
) -> list[float]:
    if _artifact_uses_interaction_relation(artifact):
        return (
            edge_context
            + source_context
            + target_context
            + _abs_difference(source_context, target_context)
            + _elementwise_product(source_context, target_context)
            + graph_context
            + source_logits
            + target_logits
            + _abs_difference(source_logits, target_logits)
            + _elementwise_product(source_logits, target_logits)
        )
    return edge_context + source_context + target_context + graph_context + source_logits + target_logits


def _artifact_uses_graph_context_relation(artifact: TinyBDGraphParserArtifact) -> bool:
    mode = str((artifact.train_config or {}).get("mode", "") or "")
    model_version = str(artifact.model_version or "")
    return mode == "graph_parser_m5" or model_version.endswith("_m5")


def _artifact_uses_interaction_relation(artifact: TinyBDGraphParserArtifact) -> bool:
    mode = str((artifact.train_config or {}).get("mode", "") or "")
    model_version = str(artifact.model_version or "")
    return (
        mode in {"graph_parser_m3", "graph_parser_m4", "graph_parser_m5"}
        or model_version.endswith("_m3")
        or model_version.endswith("_m4")
        or model_version.endswith("_m5")
    )


def _artifact_uses_keep_relation(artifact: TinyBDGraphParserArtifact) -> bool:
    mode = str((artifact.train_config or {}).get("mode", "") or "")
    model_version = str(artifact.model_version or "")
    return mode in {"graph_parser_m4", "graph_parser_m5"} or model_version.endswith("_m4") or model_version.endswith("_m5")


def _abs_difference(left: list[float], right: list[float]) -> list[float]:
    return [abs(float(a) - float(b)) for a, b in zip(left, right)]


def _elementwise_product(left: list[float], right: list[float]) -> list[float]:
    return [float(a) * float(b) for a, b in zip(left, right)]


def _keep_probability(fused: list[float], *, artifact: TinyBDGraphParserArtifact) -> float | None:
    if not _artifact_uses_keep_relation(artifact) or not artifact.keep_output_weights:
        return None
    logit = float(artifact.keep_output_bias) + sum(float(weight) * item for weight, item in zip(artifact.keep_output_weights, fused))
    return round(_sigmoid(logit), 6)


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1.0 / (1.0 + exp)
    exp = math.exp(value)
    return exp / (1.0 + exp)


def _select_relation_prediction(
    probabilities: list[float],
    labels: tuple[str, ...],
    *,
    keep_probability: float | None,
) -> tuple[str, float, float]:
    if keep_probability is None:
        best_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
        relation = labels[best_index]
        confidence = float(probabilities[best_index])
        return relation, confidence, confidence
    positive = [
        (index, float(probability))
        for index, probability in enumerate(probabilities[: len(labels)])
        if str(labels[index]) != "NONE"
    ]
    if not positive:
        return "NONE", 0.0, 0.0
    positive_mass = sum(probability for _index, probability in positive)
    if positive_mass <= 1e-12:
        return "NONE", 0.0, 0.0
    best_index, best_raw_probability = max(positive, key=lambda item: item[1])
    type_confidence = best_raw_probability / positive_mass
    combined_confidence = float(keep_probability) * float(type_confidence)
    return str(labels[best_index]), float(type_confidence), float(combined_confidence)


def _normalized_feature_values(
    features: dict[str, float],
    *,
    feature_names: tuple[str, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> list[float]:
    padded_means = means or tuple(0.0 for _ in feature_names)
    padded_scales = scales or tuple(1.0 for _ in feature_names)
    if len(padded_means) < len(feature_names):
        padded_means = tuple(padded_means) + tuple(0.0 for _ in range(len(feature_names) - len(padded_means)))
    if len(padded_scales) < len(feature_names):
        padded_scales = tuple(padded_scales) + tuple(1.0 for _ in range(len(feature_names) - len(padded_scales)))
    return [
        (float(features.get(name, 0.0) or 0.0) - mean) / (scale if abs(scale) > 1e-12 else 1.0)
        for name, mean, scale in zip(feature_names, padded_means, padded_scales)
    ]


def _hidden_layer_activations(
    values: list[float],
    *,
    hidden_weights: tuple[tuple[tuple[float, ...], ...], ...],
    hidden_biases: tuple[tuple[float, ...], ...],
) -> list[float]:
    activations = list(values)
    for weights, biases in zip(hidden_weights, hidden_biases):
        next_values: list[float] = []
        for row, bias in zip(weights, biases):
            value = float(bias) + sum(float(weight) * item for weight, item in zip(row, activations))
            next_values.append(max(0.0, value))
        activations = next_values
    return activations


def _linear_logits(
    activations: list[float],
    *,
    weights: tuple[tuple[float, ...], ...],
    bias: tuple[float, ...],
) -> list[float]:
    return [
        float(item_bias) + sum(float(weight) * item for weight, item in zip(row, activations))
        for row, item_bias in zip(weights, bias)
    ]


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
    local_run = _local_text_run_features(node, nodes)
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
        **local_run,
    }


def graph_parser_graph_features(
    source: dict[str, Any],
    target: dict[str, Any],
    nodes: list[dict[str, Any]],
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, float]:
    return graph_parser_graph_feature_map(nodes, pairs).get(
        (str(source.get("node_id", "") or ""), str(target.get("node_id", "") or "")),
        {},
    )


def graph_parser_graph_feature_map(
    nodes: list[dict[str, Any]],
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[tuple[str, str], dict[str, float]]:
    node_count = max(1, len(nodes))
    pair_count = max(1, len(pairs))
    glyph_count = sum(1 for node in nodes if node.get("node_type") == "glyph")
    vector_count = sum(1 for node in nodes if node.get("node_type") == "vector")
    span = _nodes_bbox(nodes)
    graph_width = max(1e-6, span[2] - span[0])
    graph_height = max(1e-6, span[3] - span[1])
    normalizer = max(graph_width, graph_height, 1.0)
    out_distances_by_id: dict[str, list[float]] = {}
    in_distances_by_id: dict[str, list[float]] = {}
    out_count_by_id: dict[str, int] = {}
    in_count_by_id: dict[str, int] = {}
    out_rule_neighbor_count_by_id: dict[str, int] = {}
    in_rule_neighbor_count_by_id: dict[str, int] = {}
    edge_distances: dict[tuple[str, str], float] = {}
    for left, right in pairs:
        left_id = str(left.get("node_id", "") or "")
        right_id = str(right.get("node_id", "") or "")
        distance = _node_distance(left, right, normalizer=normalizer)
        key = (left_id, right_id)
        edge_distances[key] = distance
        out_distances_by_id.setdefault(left_id, []).append(distance)
        in_distances_by_id.setdefault(right_id, []).append(distance)
        out_count_by_id[left_id] = out_count_by_id.get(left_id, 0) + 1
        in_count_by_id[right_id] = in_count_by_id.get(right_id, 0) + 1
        if right.get("node_type") == "vector":
            out_rule_neighbor_count_by_id[left_id] = out_rule_neighbor_count_by_id.get(left_id, 0) + 1
        if left.get("node_type") == "vector":
            in_rule_neighbor_count_by_id[right_id] = in_rule_neighbor_count_by_id.get(right_id, 0) + 1
    sorted_out_distances_by_id = {
        node_id: sorted(distances)
        for node_id, distances in out_distances_by_id.items()
    }
    output: dict[tuple[str, str], dict[str, float]] = {}
    for source_id, target_id in edge_distances:
        out_distances = out_distances_by_id.get(source_id, [])
        in_distances = in_distances_by_id.get(target_id, [])
        sorted_out = sorted_out_distances_by_id.get(source_id, [])
        edge_distance = edge_distances[(source_id, target_id)]
        distance_rank = _distance_rank(sorted_out, edge_distance)
        source_out_count = out_count_by_id.get(source_id, 0)
        source_in_count = in_count_by_id.get(source_id, 0)
        target_out_count = out_count_by_id.get(target_id, 0)
        target_in_count = in_count_by_id.get(target_id, 0)
        output[(source_id, target_id)] = {
            "bias": 1.0,
            "node_count": float(node_count),
            "glyph_count": float(glyph_count),
            "vector_count": float(vector_count),
            "candidate_pair_count": float(pair_count),
            "graph_width": graph_width,
            "graph_height": graph_height,
            "graph_aspect_ratio": min(1000.0, graph_width / graph_height),
            "candidate_density": float(pair_count) / float(node_count * node_count),
            "source_out_degree": float(source_out_count),
            "source_in_degree": float(source_in_count),
            "target_out_degree": float(target_out_count),
            "target_in_degree": float(target_in_count),
            "source_out_degree_ratio": float(source_out_count) / float(pair_count),
            "source_in_degree_ratio": float(source_in_count) / float(pair_count),
            "target_out_degree_ratio": float(target_out_count) / float(pair_count),
            "target_in_degree_ratio": float(target_in_count) / float(pair_count),
            "candidate_distance_rank": float(distance_rank),
            "candidate_distance_rank_ratio": float(distance_rank) / max(1.0, float(source_out_count)),
            "candidate_distance_percentile": _distance_percentile(sorted_out, edge_distance),
            "source_min_distance": min(out_distances) if out_distances else 0.0,
            "source_mean_distance": sum(out_distances) / len(out_distances) if out_distances else 0.0,
            "target_min_distance": min(in_distances) if in_distances else 0.0,
            "target_mean_distance": sum(in_distances) / len(in_distances) if in_distances else 0.0,
            "source_rule_neighbor_count": float(out_rule_neighbor_count_by_id.get(source_id, 0)),
            "target_rule_neighbor_count": float(in_rule_neighbor_count_by_id.get(target_id, 0)),
        }
    return output


def _node_distance(left: dict[str, Any], right: dict[str, Any], *, normalizer: float) -> float:
    left_center = left.get("center", [0.0, 0.0])
    right_center = right.get("center", [0.0, 0.0])
    dx = float(right_center[0]) - float(left_center[0])
    dy = float(right_center[1]) - float(left_center[1])
    return math.sqrt(dx * dx + dy * dy) / max(float(normalizer), 1e-6)


def _distance_rank(sorted_distances: list[float], value: float) -> int:
    for index, item in enumerate(sorted_distances, start=1):
        if value <= item + 1e-9:
            return index
    return len(sorted_distances)


def _distance_percentile(sorted_distances: list[float], value: float) -> float:
    if not sorted_distances:
        return 0.0
    rank = _distance_rank(sorted_distances, value)
    return float(rank - 1) / max(1.0, float(len(sorted_distances) - 1))


def _is_runtime_training_relation(relation: str) -> bool:
    return (
        relation in GRAPH_PARSER_RELATIONS
        and relation != "NONE"
        and relation not in GRAPH_PARSER_NON_RUNTIME_SUPERVISION_RELATIONS
    )


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
            if not _is_runtime_training_relation(relation):
                continue
            confidence = float(label.get("confidence", 0.0) or 0.0)
            current = positive.get((source, target))
            if _prefer_training_relation((relation, confidence), current):
                positive[(source, target)] = (relation, confidence)
        nodes = graph_nodes(row)
        for label in _structure_relation_labels_from_structure(nodes, alignment):
            relation = str(label.get("relation", "") or "")
            if not _is_runtime_training_relation(relation):
                continue
            source = str(label.get("source", "") or "")
            target = str(label.get("target", "") or "")
            confidence = float(label.get("confidence", 0.0) or 0.0)
            current = positive.get((source, target))
            if _prefer_training_relation((relation, confidence), current):
                positive[(source, target)] = (relation, confidence)
        pairs = candidate_pairs(nodes)
        node_by_id = {str(node.get("node_id", "") or ""): node for node in nodes}
        node_features_by_id = {
            str(node.get("node_id", "") or ""): graph_parser_node_features(node, nodes)
            for node in nodes
        }
        pair_keys = {(str(source["node_id"]), str(target["node_id"])) for source, target in pairs}
        for source_id, target_id in positive:
            if (source_id, target_id) in pair_keys:
                continue
            if source_id not in node_by_id or target_id not in node_by_id:
                continue
            pairs.append((node_by_id[source_id], node_by_id[target_id]))
            pair_keys.add((source_id, target_id))
        graph_features_by_pair = graph_parser_graph_feature_map(nodes, pairs)
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
                    "source_node_features": node_features_by_id.get(key[0], {}),
                    "target_node_features": node_features_by_id.get(key[1], {}),
                    "graph_features": graph_features_by_pair.get(key, {}),
                }
            )
    return samples


def _prefer_training_relation(candidate: tuple[str, float], current: tuple[str, float] | None) -> bool:
    if current is None:
        return True
    return _training_relation_rank(candidate) > _training_relation_rank(current)


def _training_relation_rank(item: tuple[str, float]) -> tuple[int, float]:
    relation, confidence = item
    priority = 0 if relation == "NEXT" else 1
    return (priority, float(confidence))


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
        role_overrides = _node_label_overrides_from_structure(alignment)
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
                if node_id in role_overrides:
                    override_label, override_confidence = role_overrides[node_id]
                    label = override_label
                    confidence = min(confidence or override_confidence, override_confidence)
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


def _node_label_overrides_from_structure(alignment: dict[str, Any]) -> dict[str, tuple[str, float]]:
    output: dict[str, tuple[str, float]] = {}
    for item in alignment.get("structure_labels", []) or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "")
        if role == "TARGET_OPERATOR_TEXT_RUN_EVIDENCE":
            label = "OPERATOR"
            node_ids = item.get("text_pdf_node_ids", [])
        elif role == "TARGET_TEXT_RUN_EVIDENCE":
            label = "TEXT"
            node_ids = item.get("text_pdf_node_ids", [])
        elif role == "TARGET_EQUATION_TAG_EVIDENCE":
            label = "EQUATION_TAG"
            node_ids = item.get("tag_pdf_node_ids", [])
        else:
            continue
        confidence = float(item.get("confidence", 1.0) or 1.0)
        for node_id in node_ids if isinstance(node_ids, (list, tuple, set)) else []:
            key = str(node_id or "")
            if not key:
                continue
            current = output.get(key)
            if current is None or confidence >= current[1]:
                output[key] = (label, confidence)
    return output


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
        elif role == "TARGET_GROUP_BOUNDARY_EVIDENCE":
            member_nodes = _nodes_for_ids(node_by_id, item.get("member_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            output.extend(_sequence_training_relations(member_nodes, confidence))
        elif role == "TARGET_FRACTION_GROUP_BOUNDARY_EVIDENCE":
            confidence = float(item.get("confidence", 1.0) or 1.0)
            output.extend(_sequence_training_relations(_nodes_for_ids(node_by_id, item.get("numerator_pdf_node_ids", [])), confidence))
            output.extend(_sequence_training_relations(_nodes_for_ids(node_by_id, item.get("denominator_pdf_node_ids", [])), confidence))
        elif role == "TARGET_SCRIPT_GROUP_BOUNDARY_EVIDENCE":
            base_nodes = _nodes_for_ids(node_by_id, item.get("base_pdf_node_ids", []))
            if not base_nodes:
                continue
            base_id = str(base_nodes[0].get("node_id", "") or "")
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for field, relation in (
                ("sub_pdf_node_ids", "SUB"),
                ("sup_pdf_node_ids", "SUP"),
                ("pre_sub_pdf_node_ids", "PRE_SUB"),
                ("pre_sup_pdf_node_ids", "PRE_SUP"),
            ):
                for member in _nodes_for_ids(node_by_id, item.get(field, [])):
                    output.append(
                        _training_relation(
                            base_id,
                            str(member.get("node_id", "") or ""),
                            relation,
                            confidence,
                        )
                    )
        elif role == "TARGET_RADICAL_GROUP_BOUNDARY_EVIDENCE":
            mark_nodes = _nodes_for_ids(node_by_id, item.get("mark_pdf_node_ids", []))
            if not mark_nodes:
                continue
            mark_id = str(mark_nodes[0].get("node_id", "") or "")
            confidence = float(item.get("confidence", 1.0) or 1.0)
            for body in _nodes_for_ids(node_by_id, item.get("body_pdf_node_ids", [])):
                output.append(
                    _training_relation(mark_id, str(body.get("node_id", "") or ""), "RADICAL_BODY", confidence)
                )
            for index in _nodes_for_ids(node_by_id, item.get("index_pdf_node_ids", [])):
                output.append(
                    _training_relation(mark_id, str(index.get("node_id", "") or ""), "RADICAL_INDEX", confidence)
                )
        elif role == "TARGET_FENCE_GROUP_BOUNDARY_EVIDENCE":
            confidence = float(item.get("confidence", 1.0) or 1.0)
            output.extend(_sequence_training_relations(_nodes_for_ids(node_by_id, item.get("body_pdf_node_ids", [])), confidence))
        elif role == "TARGET_MATRIX_ROW_GROUP_BOUNDARY_EVIDENCE":
            confidence = float(item.get("confidence", 1.0) or 1.0)
            output.extend(_sequence_training_relations(_nodes_for_ids(node_by_id, item.get("row_pdf_node_ids", [])), confidence))
        elif role == "TARGET_MATRIX_CELL_GROUP_BOUNDARY_EVIDENCE":
            cell_nodes = _nodes_for_ids(node_by_id, item.get("cell_pdf_node_ids", []))
            confidence = float(item.get("confidence", 1.0) or 1.0)
            output.extend(_cell_content_training_relations(cell_nodes, confidence))
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


def _sequence_training_relations(nodes: list[dict[str, Any]], confidence: float) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for previous, current in zip(nodes, nodes[1:]):
        output.append(
            _training_relation(
                str(previous.get("node_id", "") or ""),
                str(current.get("node_id", "") or ""),
                "NEXT",
                confidence,
            )
        )
    return output


def _cell_content_training_relations(nodes: list[dict[str, Any]], confidence: float) -> list[dict[str, Any]]:
    if not nodes:
        return []
    anchor_id = str(nodes[0].get("node_id", "") or "")
    return [
        _training_relation(anchor_id, str(member.get("node_id", "") or ""), "CELL_CONTENT", confidence)
        for member in nodes[1:]
    ]


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


def _local_text_run_features(node: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, float]:
    defaults = {
        "prev_same_font": 0.0,
        "next_same_font": 0.0,
        "prev_baseline_alignment": 0.0,
        "next_baseline_alignment": 0.0,
        "prev_horizontal_gap": 10.0,
        "next_horizontal_gap": 10.0,
        "letter_run_left": 0.0,
        "letter_run_right": 0.0,
        "letter_run_length": 0.0,
        "same_font_letter_run_length": 0.0,
    }
    if node.get("node_type") != "glyph":
        return defaults
    ordered = [
        item
        for item in sorted(nodes, key=_node_sort_key)
        if item.get("node_type") == "glyph" and str(item.get("text", "") or "").strip()
    ]
    node_id = str(node.get("node_id", "") or "")
    index_by_id = {str(item.get("node_id", "") or ""): index for index, item in enumerate(ordered)}
    index = index_by_id.get(node_id)
    if index is None:
        return defaults
    previous = ordered[index - 1] if index > 0 else None
    following = ordered[index + 1] if index + 1 < len(ordered) else None
    if previous is not None:
        defaults["prev_same_font"] = 1.0 if _same_font(node, previous) else 0.0
        defaults["prev_baseline_alignment"] = _baseline_alignment(node, previous)
        defaults["prev_horizontal_gap"] = _horizontal_gap(previous, node)
    if following is not None:
        defaults["next_same_font"] = 1.0 if _same_font(node, following) else 0.0
        defaults["next_baseline_alignment"] = _baseline_alignment(node, following)
        defaults["next_horizontal_gap"] = _horizontal_gap(node, following)
    left, right, same_font_run = _letter_run_lengths(ordered, index)
    defaults["letter_run_left"] = float(left)
    defaults["letter_run_right"] = float(right)
    defaults["letter_run_length"] = float(left + 1 + right) if _is_letter_glyph(node) else 0.0
    defaults["same_font_letter_run_length"] = float(same_font_run)
    return defaults


def _letter_run_lengths(nodes: list[dict[str, Any]], index: int) -> tuple[int, int, int]:
    node = nodes[index]
    if not _is_letter_glyph(node):
        return 0, 0, 0
    left = 0
    cursor = index - 1
    while cursor >= 0 and _is_text_run_neighbor(nodes[cursor], nodes[cursor + 1]):
        left += 1
        cursor -= 1
    right = 0
    cursor = index + 1
    while cursor < len(nodes) and _is_text_run_neighbor(nodes[cursor - 1], nodes[cursor]):
        right += 1
        cursor += 1
    same_font = 1
    cursor = index - 1
    while cursor >= 0 and _is_text_run_neighbor(nodes[cursor], nodes[cursor + 1]) and _same_font(nodes[cursor], node):
        same_font += 1
        cursor -= 1
    cursor = index + 1
    while cursor < len(nodes) and _is_text_run_neighbor(nodes[cursor - 1], nodes[cursor]) and _same_font(nodes[cursor], node):
        same_font += 1
        cursor += 1
    return left, right, same_font


def _is_text_run_neighbor(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not (_is_letter_glyph(left) and _is_letter_glyph(right)):
        return False
    if _baseline_alignment(left, right) < 0.72:
        return False
    return _horizontal_gap(left, right) <= 1.25


def _is_letter_glyph(node: dict[str, Any]) -> bool:
    text = str(node.get("text", "") or "")
    return bool(text) and any(unicodedata.category(char).startswith("L") for char in text)


def _same_font(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return str(first.get("font", "") or "") == str(second.get("font", "") or "")


def _baseline_alignment(first: dict[str, Any], second: dict[str, Any]) -> float:
    first_box = first.get("bbox", [0.0, 0.0, 0.0, 0.0])
    second_box = second.get("bbox", [0.0, 0.0, 0.0, 0.0])
    if not isinstance(first_box, (list, tuple)) or not isinstance(second_box, (list, tuple)):
        return 0.0
    first_height = max(1e-6, float(first_box[3]) - float(first_box[1]))
    second_height = max(1e-6, float(second_box[3]) - float(second_box[1]))
    first_center = (float(first_box[1]) + float(first_box[3])) / 2.0
    second_center = (float(second_box[1]) + float(second_box[3])) / 2.0
    return round(max(0.0, 1.0 - (abs(first_center - second_center) / max(first_height, second_height, 1e-6))), 6)


def _horizontal_gap(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_box = left.get("bbox", [0.0, 0.0, 0.0, 0.0])
    right_box = right.get("bbox", [0.0, 0.0, 0.0, 0.0])
    if not isinstance(left_box, (list, tuple)) or not isinstance(right_box, (list, tuple)):
        return 10.0
    left_height = max(1e-6, float(left_box[3]) - float(left_box[1]))
    right_height = max(1e-6, float(right_box[3]) - float(right_box[1]))
    gap = float(right_box[0]) - float(left_box[2])
    return round(max(0.0, gap) / max(left_height, right_height, 1e-6), 6)


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
                "reason": "graph_parser_prediction",
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
        "keep_threshold": float(predictions.get("keep_threshold", 0.5) or 0.5),
        "graph_confidence": float(predictions.get("graph_confidence", 0.0) or 0.0),
        "verifier_warnings": sorted(set(warnings)),
        "model_version": str(predictions.get("model_version", "") or ""),
    }


def _top_relation_alternatives(
    probabilities: list[float],
    labels: tuple[str, ...],
    *,
    keep_probability: float | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    alternatives: list[dict[str, Any]] = []
    scored: list[tuple[int, float]] = []
    if keep_probability is None:
        scored = [
            (index, float(probability))
            for index, probability in enumerate(probabilities[: len(labels)])
        ]
    else:
        positive = [
            (index, float(probability))
            for index, probability in enumerate(probabilities[: len(labels)])
            if str(labels[index]) != "NONE"
        ]
        positive_mass = sum(probability for _index, probability in positive)
        if positive_mass > 1e-12:
            scored = [
                (index, float(keep_probability) * (probability / positive_mass))
                for index, probability in positive
            ]
    for index, probability in sorted(
        scored,
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
        "vector_type": str(item.get("vector_type", "") or item.get("type", "") or item.get("kind", "") or ""),
        "is_horizontal_rule_candidate": bool(item.get("is_horizontal_rule_candidate", False)),
        "is_vertical_rule_candidate": bool(item.get("is_vertical_rule_candidate", False)),
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
