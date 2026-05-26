"""Dependency-light edge relation baseline for TinyBDMath.

This trains on graph row candidate-edge features plus weak relation labels.
It is a bootstrapping model for the relation pipeline, not an accepted formula
recognizer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any


EDGE_BASELINE_VERSION = "tinybdmath_edge_softmax_v2_geometry_vector_rule_radical"
EDGE_FEATURES = (
    "bias",
    "dx_over_height",
    "dy_over_height",
    "horizontal_gap_over_height",
    "vertical_gap_over_height",
    "x_overlap",
    "y_overlap",
    "size_ratio",
    "width_ratio",
    "height_ratio",
    "same_font",
    "source_unknown",
    "target_unknown",
    "source_is_script_size",
    "target_is_script_size",
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
)
EDGE_LABELS = (
    "HORIZONTAL",
    "SUP",
    "SUB",
    "ABOVE",
    "BELOW",
    "FRACTION_BAR",
    "OVERLINE",
    "RADICAL_BODY",
    "NO_RELATION",
)


@dataclass(frozen=True)
class TinyBDEdgeBaselineModel:
    version: str
    feature_names: tuple[str, ...]
    labels: tuple[str, ...]
    weights: tuple[tuple[float, ...], ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    train_config: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "TinyBDEdgeBaselineModel":
        return cls(
            version=str(payload.get("version", EDGE_BASELINE_VERSION)),
            feature_names=tuple(str(item) for item in payload.get("feature_names", EDGE_FEATURES)),
            labels=tuple(str(item) for item in payload.get("labels", EDGE_LABELS)),
            weights=tuple(tuple(float(v) for v in row) for row in payload.get("weights", [])),
            means=tuple(float(v) for v in payload.get("means", [])),
            scales=tuple(float(v) for v in payload.get("scales", [])),
            train_config=dict(payload.get("train_config", {})),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "TinyBDEdgeBaselineModel":
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def predict_proba(self, edge: dict[str, Any]) -> dict[str, float]:
        feature_names = self.feature_names or EDGE_FEATURES
        vector = _normalize(edge_features(edge), self.means, self.scales, feature_names=feature_names)
        probs = _softmax([_dot(weights, vector) for weights in self.weights])
        return {label: round(probs[index], 6) for index, label in enumerate(self.labels)}

    def predict(self, edge: dict[str, Any]) -> str:
        probs = self.predict_proba(edge)
        return max(probs.items(), key=lambda item: item[1])[0] if probs else "NO_RELATION"


def train_edge_baseline(
    samples: list[dict[str, Any]],
    *,
    epochs: int = 8,
    learning_rate: float = 0.04,
    l2: float = 0.0001,
    seed: int = 23,
) -> tuple[TinyBDEdgeBaselineModel, dict[str, Any]]:
    samples = [sample for sample in samples if sample.get("label") in EDGE_LABELS]
    if not samples:
        raise ValueError("no edge samples to train")
    train, validation = _split(samples, seed=seed)
    feature_vectors = [_vector(sample) for sample in train]
    means, scales = _stats(feature_vectors)
    label_to_index = {label: index for index, label in enumerate(EDGE_LABELS)}
    weights = _init_weights(len(EDGE_LABELS), len(EDGE_FEATURES), seed)
    for _epoch in range(max(1, int(epochs))):
        for sample in train:
            vector = _normalize(edge_features(sample), means, scales)
            expected = label_to_index[str(sample.get("label"))]
            probs = _softmax([_dot(row, vector) for row in weights])
            for class_index, row in enumerate(weights):
                target = 1.0 if class_index == expected else 0.0
                error = probs[class_index] - target
                for feature_index, value in enumerate(vector):
                    row[feature_index] -= learning_rate * (error * value + l2 * row[feature_index])
    model = TinyBDEdgeBaselineModel(
        version=EDGE_BASELINE_VERSION,
        feature_names=EDGE_FEATURES,
        labels=EDGE_LABELS,
        weights=tuple(tuple(round(v, 8) for v in row) for row in weights),
        means=tuple(round(v, 8) for v in means),
        scales=tuple(round(v, 8) for v in scales),
        train_config={"epochs": int(epochs), "learning_rate": float(learning_rate), "l2": float(l2), "seed": int(seed)},
    )
    report = {
        "schema_version": "tinybdmath_edge_baseline_report_v1",
        "model_version": EDGE_BASELINE_VERSION,
        "samples": len(samples),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "train": evaluate_edge_baseline(model, train),
        "validation": evaluate_edge_baseline(model, validation),
        "label_counts": _label_counts(samples),
        "warning": "Trained on weak relation labels; use as candidate evidence only.",
    }
    return model, report


def edge_features(edge: dict[str, Any]) -> dict[str, float]:
    features = edge.get("features", {})
    if not isinstance(features, dict):
        features = {}
    hint = str(edge.get("hint", ""))
    return {
        "bias": 1.0,
        "dx_over_height": _float(features.get("dx_over_height")),
        "dy_over_height": _float(features.get("dy_over_height")),
        "horizontal_gap_over_height": _float(features.get("horizontal_gap_over_height")),
        "vertical_gap_over_height": _float(features.get("vertical_gap_over_height")),
        "x_overlap": _float(features.get("x_overlap")),
        "y_overlap": _float(features.get("y_overlap")),
        "size_ratio": _float(features.get("size_ratio")),
        "width_ratio": _float(features.get("width_ratio")),
        "height_ratio": _float(features.get("height_ratio")),
        "same_font": _float(features.get("same_font")),
        "source_unknown": _float(features.get("source_unknown")),
        "target_unknown": _float(features.get("target_unknown")),
        "source_is_script_size": _float(features.get("source_is_script_size")),
        "target_is_script_size": _float(features.get("target_is_script_size")),
        "hint_right_neighbor": 1.0 if hint == "right_neighbor" else 0.0,
        "hint_superscript_zone": 1.0 if hint == "superscript_zone" else 0.0,
        "hint_subscript_zone": 1.0 if hint == "subscript_zone" else 0.0,
        "hint_above_zone": 1.0 if hint in {"above_zone", "above_rule_candidate"} else 0.0,
        "hint_below_zone": 1.0 if hint in {"below_zone", "below_rule_candidate"} else 0.0,
        "hint_far_context": 1.0 if hint == "far_context" else 0.0,
        "hint_rule": 1.0 if "rule" in hint or "fraction_bar" in hint else 0.0,
        "hint_above_rule": 1.0 if hint == "above_rule_candidate" else 0.0,
        "hint_below_rule": 1.0 if hint == "below_rule_candidate" else 0.0,
        "hint_fraction_bar": 1.0 if hint == "fraction_bar_candidate" else 0.0,
        "hint_overline": 1.0 if hint == "overline_candidate" else 0.0,
        "hint_radical_body": 1.0 if hint == "radical_body_candidate" else 0.0,
    }


def evaluate_edge_baseline(model: TinyBDEdgeBaselineModel, samples: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = {label: {other: 0 for other in model.labels} for label in model.labels}
    correct = 0
    for sample in samples:
        expected = str(sample.get("label"))
        predicted = model.predict(sample)
        if predicted == expected:
            correct += 1
        confusion.setdefault(expected, {other: 0 for other in model.labels})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
    return {"samples": len(samples), "accuracy": round(correct / len(samples), 6) if samples else 1.0, "confusion": confusion}


def _vector(edge: dict[str, Any]) -> list[float]:
    features = edge_features(edge)
    return [float(features[name]) for name in EDGE_FEATURES]


def _normalize(
    features: dict[str, float],
    means: tuple[float, ...],
    scales: tuple[float, ...],
    *,
    feature_names: tuple[str, ...] = EDGE_FEATURES,
) -> list[float]:
    values = [float(features.get(name, 0.0)) for name in feature_names]
    if not means or not scales:
        return values
    normalized: list[float] = []
    for index, value in enumerate(values):
        mean = means[index] if index < len(means) else 0.0
        scale = scales[index] if index < len(scales) else 1.0
        normalized.append((value - mean) / max(scale, 1e-6))
    return normalized


def _stats(vectors: list[list[float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    width = len(EDGE_FEATURES)
    if not vectors:
        return tuple(0.0 for _ in range(width)), tuple(1.0 for _ in range(width))
    means = [sum(row[index] for row in vectors) / len(vectors) for index in range(width)]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in vectors) / len(vectors)
        scales.append(math.sqrt(max(variance, 1e-12)))
    return tuple(means), tuple(scales)


def _split(samples: list[dict[str, Any]], *, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    import hashlib

    for sample in samples:
        key = f"{seed}:{sample.get('row_id','')}:{sample.get('edge_id','')}"
        bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 10
        (validation if bucket == 0 else train).append(sample)
    if not validation and train:
        validation.append(train.pop())
    return train, validation


def _init_weights(classes: int, features: int, seed: int) -> list[list[float]]:
    state = int(seed) & 0xFFFFFFFF
    result: list[list[float]] = []
    for _class in range(classes):
        row: list[float] = []
        for _feature in range(features):
            state = (1664525 * state + 1013904223) & 0xFFFFFFFF
            row.append(((state / 0xFFFFFFFF) - 0.5) * 0.02)
        result.append(row)
    return result


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    offset = max(logits)
    exps = [math.exp(value - offset) for value in logits]
    total = sum(exps) or 1.0
    return [value / total for value in exps]


def _dot(weights: list[float] | tuple[float, ...], vector: list[float]) -> float:
    return sum(float(weight) * float(value) for weight, value in zip(weights, vector))


def _label_counts(samples: list[dict[str, Any]]) -> dict[str, int]:
    counts = {label: 0 for label in EDGE_LABELS}
    for sample in samples:
        label = str(sample.get("label"))
        if label in counts:
            counts[label] += 1
    return counts


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
