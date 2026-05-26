"""Dependency-light TinyBDMath graph baseline.

This baseline is intentionally modest: it learns a coarse structural bucket
from graph statistics so the dataset/model artifact/evaluation pipeline is
executable before relation-level GNN labels are ready.  It is not a LaTeX
decoder and must not be used for accepted formula output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any


GRAPH_BASELINE_VERSION = "tinybdmath_graph_softmax_v0"
GRAPH_BASELINE_FEATURES = (
    "bias",
    "glyph_count_log",
    "vector_count_log",
    "edge_count_log",
    "math_font_rate",
    "script_size_rate",
    "horizontal_rule_count_log",
    "right_neighbor_rate",
    "subscript_zone_rate",
    "superscript_zone_rate",
    "above_below_rate",
    "far_context_rate",
    "font_count_log",
    "is_inline",
    "is_display",
)
GRAPH_BASELINE_LABELS = (
    "single_or_short",
    "linear",
    "script",
    "fraction_or_rule",
    "complex_layout",
    "math_alphabet",
)


@dataclass(frozen=True)
class TinyBDGraphBaselineModel:
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
    def from_json(cls, payload: dict[str, Any]) -> "TinyBDGraphBaselineModel":
        return cls(
            version=str(payload.get("version", GRAPH_BASELINE_VERSION)),
            feature_names=tuple(str(item) for item in payload.get("feature_names", GRAPH_BASELINE_FEATURES)),
            labels=tuple(str(item) for item in payload.get("labels", GRAPH_BASELINE_LABELS)),
            weights=tuple(tuple(float(v) for v in row) for row in payload.get("weights", [])),
            means=tuple(float(v) for v in payload.get("means", [])),
            scales=tuple(float(v) for v in payload.get("scales", [])),
            train_config=dict(payload.get("train_config", {})),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "TinyBDGraphBaselineModel":
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def predict_proba(self, row: dict[str, Any]) -> dict[str, float]:
        vector = _normalize_vector(graph_row_features(row), self.means, self.scales)
        logits = [_dot(weights, vector) for weights in self.weights]
        probs = _softmax(logits)
        return {label: round(probs[index], 6) for index, label in enumerate(self.labels)}

    def predict(self, row: dict[str, Any]) -> str:
        probs = self.predict_proba(row)
        return max(probs.items(), key=lambda item: item[1])[0] if probs else "unknown"


def graph_row_features(row: dict[str, Any]) -> dict[str, float]:
    stats = row.get("graph_stats", {})
    if not isinstance(stats, dict):
        stats = {}
    edge_counts = stats.get("edge_hint_counts", {})
    if not isinstance(edge_counts, dict):
        edge_counts = {}
    glyph_count = _float(stats.get("glyph_count"))
    vector_count = _float(stats.get("vector_count"))
    edge_count = _float(stats.get("edge_count"))
    math_font_glyphs = _float(stats.get("math_font_glyphs"))
    script_size_glyphs = _float(stats.get("script_size_glyphs"))
    font_count = _float(stats.get("font_count"))
    above = _float(edge_counts.get("above_zone")) + _float(edge_counts.get("below_zone"))
    return {
        "bias": 1.0,
        "glyph_count_log": math.log1p(glyph_count),
        "vector_count_log": math.log1p(vector_count),
        "edge_count_log": math.log1p(edge_count),
        "math_font_rate": math_font_glyphs / max(glyph_count, 1.0),
        "script_size_rate": script_size_glyphs / max(glyph_count, 1.0),
        "horizontal_rule_count_log": math.log1p(_float(stats.get("horizontal_rule_candidates"))),
        "right_neighbor_rate": _float(edge_counts.get("right_neighbor")) / max(edge_count, 1.0),
        "subscript_zone_rate": _float(edge_counts.get("subscript_zone")) / max(edge_count, 1.0),
        "superscript_zone_rate": _float(edge_counts.get("superscript_zone")) / max(edge_count, 1.0),
        "above_below_rate": above / max(edge_count, 1.0),
        "far_context_rate": _float(edge_counts.get("far_context")) / max(edge_count, 1.0),
        "font_count_log": math.log1p(font_count),
        "is_inline": 1.0 if str(row.get("kind", "")) == "inline" else 0.0,
        "is_display": 1.0 if str(row.get("kind", "")) == "display" else 0.0,
    }


def graph_row_weak_label(row: dict[str, Any]) -> str:
    tags = set(str(item) for item in row.get("coverage_tags", []) if item)
    if {"matrix_or_array", "cases", "alignment"} & tags:
        return "complex_layout"
    if "fraction" in tags or "horizontal_rule" in tags or "radical" in tags:
        return "fraction_or_rule"
    if "math_alphabet" in tags:
        return "math_alphabet"
    if "subscript" in tags or "superscript" in tags or "subsup" in tags or "script_size_pdf_evidence" in tags:
        return "script"
    if "single_glyph_or_empty_text" in tags:
        return "single_or_short"
    return "linear"


def train_graph_baseline(
    rows: list[dict[str, Any]],
    *,
    epochs: int = 24,
    learning_rate: float = 0.05,
    l2: float = 0.0001,
    seed: int = 17,
) -> tuple[TinyBDGraphBaselineModel, dict[str, Any]]:
    if not rows:
        raise ValueError("no graph rows to train")
    train_rows, validation_rows = deterministic_split(rows, seed=seed)
    feature_vectors = [_feature_vector(row) for row in train_rows]
    means, scales = _feature_stats(feature_vectors)
    label_to_index = {label: index for index, label in enumerate(GRAPH_BASELINE_LABELS)}
    weights = _init_weights(len(GRAPH_BASELINE_LABELS), len(GRAPH_BASELINE_FEATURES), seed)
    for _epoch in range(max(1, int(epochs))):
        for row in train_rows:
            vector = _normalize_vector(graph_row_features(row), means, scales)
            label_index = label_to_index[graph_row_weak_label(row)]
            probs = _softmax([_dot(class_weights, vector) for class_weights in weights])
            for class_index, class_weights in enumerate(weights):
                target = 1.0 if class_index == label_index else 0.0
                error = probs[class_index] - target
                for feature_index, value in enumerate(vector):
                    class_weights[feature_index] -= learning_rate * (error * value + l2 * class_weights[feature_index])
    model = TinyBDGraphBaselineModel(
        version=GRAPH_BASELINE_VERSION,
        feature_names=GRAPH_BASELINE_FEATURES,
        labels=GRAPH_BASELINE_LABELS,
        weights=tuple(tuple(round(value, 8) for value in row) for row in weights),
        means=tuple(round(value, 8) for value in means),
        scales=tuple(round(value, 8) for value in scales),
        train_config={
            "epochs": int(epochs),
            "learning_rate": float(learning_rate),
            "l2": float(l2),
            "seed": int(seed),
            "label_source": "coverage_weak_labels_for_pipeline_smoke",
        },
    )
    report = {
        "schema_version": "tinybdmath_graph_baseline_report_v1",
        "model_version": GRAPH_BASELINE_VERSION,
        "rows": len(rows),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "train": evaluate_graph_baseline(model, train_rows),
        "validation": evaluate_graph_baseline(model, validation_rows),
        "label_counts": _label_counts(rows),
        "warning": "Weak labels are for model pipeline smoke; relation-level labels are still required for formula reconstruction.",
    }
    return model, report


def deterministic_split(rows: list[dict[str, Any]], *, seed: int = 17) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for row in rows:
        key = f"{seed}:{row.get('row_id', '')}:{row.get('case', '')}:{row.get('page_num', '')}"
        bucket = int(__import__("hashlib").sha256(key.encode("utf-8")).hexdigest()[:8], 16) % 10
        (validation if bucket == 0 else train).append(row)
    if not validation and train:
        validation.append(train.pop())
    return train, validation


def evaluate_graph_baseline(model: TinyBDGraphBaselineModel, rows: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = {label: {other: 0 for other in model.labels} for label in model.labels}
    correct = 0
    for row in rows:
        expected = graph_row_weak_label(row)
        predicted = model.predict(row)
        if predicted == expected:
            correct += 1
        confusion.setdefault(expected, {other: 0 for other in model.labels})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
    return {
        "rows": len(rows),
        "accuracy": round(correct / len(rows), 6) if rows else 1.0,
        "confusion": confusion,
    }


def _feature_vector(row: dict[str, Any]) -> list[float]:
    features = graph_row_features(row)
    return [float(features[name]) for name in GRAPH_BASELINE_FEATURES]


def _normalize_vector(features: dict[str, float], means: tuple[float, ...], scales: tuple[float, ...]) -> list[float]:
    vector = [float(features.get(name, 0.0)) for name in GRAPH_BASELINE_FEATURES]
    if not means or not scales:
        return vector
    return [(value - means[index]) / max(scales[index], 1e-6) for index, value in enumerate(vector)]


def _feature_stats(vectors: list[list[float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    width = len(GRAPH_BASELINE_FEATURES)
    if not vectors:
        return tuple(0.0 for _ in range(width)), tuple(1.0 for _ in range(width))
    means = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(width)]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((vector[index] - mean) ** 2 for vector in vectors) / len(vectors)
        scales.append(math.sqrt(max(variance, 1e-12)))
    return tuple(means), tuple(scales)


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


def _dot(weights: tuple[float, ...] | list[float], vector: list[float]) -> float:
    return sum(float(weight) * float(value) for weight, value in zip(weights, vector))


def _label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {label: 0 for label in GRAPH_BASELINE_LABELS}
    for row in rows:
        counts[graph_row_weak_label(row)] += 1
    return counts


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
