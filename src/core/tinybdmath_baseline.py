"""Dependency-light TinyBDMath baseline models.

This module gives the born-digital PDF path a real train/evaluate contract
before heavier PyTorch/GNN workers are introduced.  The model is a small
one-hidden-layer MLP implemented with the Python standard library.  It predicts
candidate quality from PDF-derived graph features only; LaTeX source similarity
is used for labels and evaluation, never as an inference feature.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import random
from pathlib import Path
from typing import Any


LABELS = (
    "strong_alignment",
    "near_alignment",
    "weak_alignment",
    "low_alignment",
    "needs_symbol_repair",
    "unusable_empty_features",
    "weak_with_unknown_source",
)

FEATURE_NAMES = (
    "glyph_count_log",
    "edge_count_log",
    "edge_per_glyph",
    "unknown_glyph_rate",
    "repaired_count_log",
    "feature_density",
    "structural_signal_rate",
    "right_neighbor_rate",
    "subscript_rate",
    "superscript_rate",
    "above_rate",
    "below_rate",
    "overlap_rate",
    "far_context_rate",
    "pdf_text_len_log",
    "pdf_digit_rate",
    "pdf_operator_rate",
)


@dataclass(frozen=True)
class TinyBDBaselineConfig:
    model_version: str = "tinybdmath_stdlib_mlp_quality_v0"
    epochs: int = 120
    hidden_units: int = 24
    learning_rate: float = 0.015
    l2: float = 0.0005
    validation_fraction: float = 0.20
    seed: int = 20260525
    min_gate_confidence: float = 0.995


@dataclass(frozen=True)
class TinyBDBaselineModel:
    model_version: str
    labels: tuple[str, ...]
    feature_names: tuple[str, ...]
    input_hidden_weights: tuple[tuple[float, ...], ...]
    hidden_bias: tuple[float, ...]
    hidden_output_weights: tuple[tuple[float, ...], ...]
    output_bias: tuple[float, ...]
    feature_mean: tuple[float, ...]
    feature_scale: tuple[float, ...]
    config: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "TinyBDBaselineModel":
        return cls(
            model_version=str(payload.get("model_version", "")),
            labels=tuple(str(item) for item in payload.get("labels", ())),
            feature_names=tuple(str(item) for item in payload.get("feature_names", ())),
            input_hidden_weights=_tuple2(payload.get("input_hidden_weights", ())),
            hidden_bias=tuple(float(value) for value in payload.get("hidden_bias", ())),
            hidden_output_weights=_tuple2(payload.get("hidden_output_weights", ())),
            output_bias=tuple(float(value) for value in payload.get("output_bias", ())),
            feature_mean=tuple(float(value) for value in payload.get("feature_mean", ())),
            feature_scale=tuple(float(value) for value in payload.get("feature_scale", ())),
            config=dict(payload.get("config", {}) if isinstance(payload.get("config"), dict) else {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "TinyBDBaselineModel":
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))

    def predict_proba(self, features: dict[str, float]) -> dict[str, float]:
        vector = self._normalized_vector(features)
        hidden = _hidden_activation(self.input_hidden_weights, self.hidden_bias, vector)
        logits = _output_logits(self.hidden_output_weights, self.output_bias, hidden)
        probs = _softmax(logits)
        return {label: round(prob, 6) for label, prob in zip(self.labels, probs, strict=False)}

    def predict(self, features: dict[str, float]) -> str:
        probs = self.predict_proba(features)
        return max(probs, key=lambda key: probs[key]) if probs else ""

    def gate_decision(
        self,
        features: dict[str, float],
        *,
        accept_labels: tuple[str, ...] = ("strong_alignment",),
        min_confidence: float | None = None,
    ) -> dict[str, Any]:
        probs = self.predict_proba(features)
        label = max(probs, key=lambda key: probs[key]) if probs else ""
        confidence = probs.get(label, 0.0)
        threshold = float(
            min_confidence
            if min_confidence is not None
            else self.config.get("min_gate_confidence", 0.995)
        )
        return {
            "label": label,
            "confidence": round(confidence, 6),
            "accepted_candidate": label in accept_labels and confidence >= threshold,
            "threshold": threshold,
            "probabilities": probs,
        }

    def _normalized_vector(self, features: dict[str, float]) -> list[float]:
        raw = [float(features.get(name, 0.0) or 0.0) for name in self.feature_names]
        vector: list[float] = []
        for index, value in enumerate(raw):
            mean = self.feature_mean[index] if index < len(self.feature_mean) else 0.0
            scale = self.feature_scale[index] if index < len(self.feature_scale) else 1.0
            vector.append((value - mean) / max(scale, 1e-6))
        return vector


def row_features(row: dict[str, Any]) -> dict[str, float]:
    """Return inference-safe features derived from PDF graph evidence only."""

    hints = row.get("edge_hint_counts", {})
    if not isinstance(hints, dict):
        hints = {}
    glyph_count = _float(row.get("glyph_count"))
    edge_count = _float(row.get("edge_count"))
    denom = max(edge_count, 1.0)
    pdf_text = str(row.get("pdf_text", "") or "")
    return {
        "glyph_count_log": math.log1p(max(glyph_count, 0.0)),
        "edge_count_log": math.log1p(max(edge_count, 0.0)),
        "edge_per_glyph": edge_count / glyph_count if glyph_count else 0.0,
        "unknown_glyph_rate": _float(row.get("unknown_glyph_rate")),
        "repaired_count_log": math.log1p(max(_float(row.get("repaired_count")), 0.0)),
        "feature_density": _float(row.get("feature_density")),
        "structural_signal_rate": _float(row.get("structural_signal_count")) / denom,
        "right_neighbor_rate": _float(hints.get("right_neighbor")) / denom,
        "subscript_rate": _float(hints.get("subscript_zone")) / denom,
        "superscript_rate": _float(hints.get("superscript_zone")) / denom,
        "above_rate": _float(hints.get("above_zone")) / denom,
        "below_rate": _float(hints.get("below_zone")) / denom,
        "overlap_rate": _float(hints.get("overlap_zone")) / denom,
        "far_context_rate": _float(hints.get("far_context")) / denom,
        "pdf_text_len_log": math.log1p(len(pdf_text.strip())),
        "pdf_digit_rate": _char_rate(pdf_text, set("0123456789")),
        "pdf_operator_rate": _operator_rate(pdf_text),
    }


def train_validation_split(
    rows: list[dict[str, Any]],
    *,
    validation_fraction: float = 0.20,
    seed: int = 20260525,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not rows:
        return [], []
    rng = random.Random(seed)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("quality_label", "")), []).append(row)
    train: list[dict[str, Any]] = []
    validation: list[dict[str, Any]] = []
    for label_rows in grouped.values():
        shuffled = list(label_rows)
        rng.shuffle(shuffled)
        holdout = int(round(len(shuffled) * max(0.0, min(validation_fraction, 0.8))))
        if len(shuffled) > 1:
            holdout = max(1, holdout)
        else:
            holdout = 0
        validation.extend(shuffled[:holdout])
        train.extend(shuffled[holdout:])
    rng.shuffle(train)
    rng.shuffle(validation)
    if not train and validation:
        train.append(validation.pop())
    return train, validation


def train_baseline(
    rows: list[dict[str, Any]],
    config: TinyBDBaselineConfig | None = None,
) -> tuple[TinyBDBaselineModel, dict[str, Any]]:
    config = config or TinyBDBaselineConfig()
    feature_names = FEATURE_NAMES
    labels = LABELS
    label_index = {label: index for index, label in enumerate(labels)}
    feature_rows = [row_features(row) for row in rows]
    means, scales = _feature_stats(feature_rows, feature_names)
    training_rows = [
        (
            _normalize([features.get(name, 0.0) for name in feature_names], means, scales),
            label_index.get(str(row.get("quality_label", "")), label_index["low_alignment"]),
        )
        for row, features in zip(rows, feature_rows, strict=False)
    ]

    rng = random.Random(config.seed)
    input_hidden, hidden_bias, hidden_output, output_bias = _init_weights(
        input_size=len(feature_names),
        hidden_size=max(2, int(config.hidden_units)),
        output_size=len(labels),
        rng=rng,
    )
    for _epoch in range(max(1, int(config.epochs))):
        rng.shuffle(training_rows)
        for vector, target in training_rows:
            hidden_raw = [
                sum(weight * value for weight, value in zip(row, vector, strict=False)) + hidden_bias[index]
                for index, row in enumerate(input_hidden)
            ]
            hidden = [math.tanh(value) for value in hidden_raw]
            logits = [
                sum(weight * value for weight, value in zip(row, hidden, strict=False)) + output_bias[index]
                for index, row in enumerate(hidden_output)
            ]
            probs = _softmax(logits)

            grad_logits = [
                probs[index] - (1.0 if index == target else 0.0)
                for index in range(len(labels))
            ]
            old_hidden_output = [row[:] for row in hidden_output]
            for out_id, grad in enumerate(grad_logits):
                for hidden_id, hidden_value in enumerate(hidden):
                    hidden_output[out_id][hidden_id] -= config.learning_rate * (
                        grad * hidden_value + config.l2 * hidden_output[out_id][hidden_id]
                    )
                output_bias[out_id] -= config.learning_rate * grad

            grad_hidden: list[float] = []
            for hidden_id, hidden_value in enumerate(hidden):
                downstream = sum(
                    grad_logits[out_id] * old_hidden_output[out_id][hidden_id]
                    for out_id in range(len(labels))
                )
                grad_hidden.append(downstream * (1.0 - hidden_value * hidden_value))
            for hidden_id, grad in enumerate(grad_hidden):
                for feature_id, value in enumerate(vector):
                    input_hidden[hidden_id][feature_id] -= config.learning_rate * (
                        grad * value + config.l2 * input_hidden[hidden_id][feature_id]
                    )
                hidden_bias[hidden_id] -= config.learning_rate * grad

    model = TinyBDBaselineModel(
        model_version=config.model_version,
        labels=labels,
        feature_names=feature_names,
        input_hidden_weights=_round2(input_hidden),
        hidden_bias=tuple(round(value, 10) for value in hidden_bias),
        hidden_output_weights=_round2(hidden_output),
        output_bias=tuple(round(value, 10) for value in output_bias),
        feature_mean=tuple(round(value, 10) for value in means),
        feature_scale=tuple(round(value, 10) for value in scales),
        config=asdict(config),
    )
    metrics = evaluate_baseline(model, rows)
    return model, metrics


def evaluate_baseline(model: TinyBDBaselineModel, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "rows": 0,
            "accuracy": 1.0,
            "label_counts": {},
            "confusion": {},
            "per_label": {},
            "gate": gate_metrics(model, rows),
        }
    correct = 0
    confusion: dict[str, dict[str, int]] = {}
    label_counts: dict[str, int] = {}
    for row in rows:
        truth = str(row.get("quality_label", ""))
        pred = model.predict(row_features(row))
        if truth == pred:
            correct += 1
        label_counts[truth] = label_counts.get(truth, 0) + 1
        confusion.setdefault(truth, {})
        confusion[truth][pred] = confusion[truth].get(pred, 0) + 1
    return {
        "rows": len(rows),
        "accuracy": round(correct / len(rows), 4),
        "label_counts": dict(sorted(label_counts.items())),
        "confusion": confusion,
        "per_label": _per_label_metrics(confusion, model.labels),
        "gate": gate_metrics(model, rows),
    }


def gate_metrics(
    model: TinyBDBaselineModel,
    rows: list[dict[str, Any]],
    *,
    accept_labels: tuple[str, ...] = ("strong_alignment",),
    min_confidence: float | None = None,
) -> dict[str, Any]:
    proposed = 0
    true_positive = 0
    false_positive = 0
    accept_truth_total = 0
    examples: list[dict[str, Any]] = []
    for row in rows:
        truth = str(row.get("quality_label", ""))
        if truth in accept_labels:
            accept_truth_total += 1
        decision = model.gate_decision(
            row_features(row),
            accept_labels=accept_labels,
            min_confidence=min_confidence,
        )
        if not decision["accepted_candidate"]:
            continue
        proposed += 1
        if truth in accept_labels:
            true_positive += 1
        else:
            false_positive += 1
            if len(examples) < 20:
                examples.append(
                    {
                        "case": row.get("case", ""),
                        "candidate_id": row.get("candidate_id", ""),
                        "truth": truth,
                        "predicted": decision["label"],
                        "confidence": decision["confidence"],
                        "pdf_text": str(row.get("pdf_text", ""))[:120],
                        "latex_target": str(row.get("latex_target", ""))[:120],
                    }
                )
    precision = true_positive / proposed if proposed else 1.0
    recall = true_positive / accept_truth_total if accept_truth_total else 1.0
    return {
        "accept_labels": list(accept_labels),
        "min_confidence": float(
            min_confidence
            if min_confidence is not None
            else model.config.get("min_gate_confidence", 0.995)
        ),
        "proposed_accepts": proposed,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "false_positive_samples": examples,
    }


def _init_weights(
    *,
    input_size: int,
    hidden_size: int,
    output_size: int,
    rng: random.Random,
) -> tuple[list[list[float]], list[float], list[list[float]], list[float]]:
    input_scale = math.sqrt(2.0 / max(input_size, 1))
    output_scale = math.sqrt(2.0 / max(hidden_size, 1))
    input_hidden = [
        [rng.uniform(-input_scale, input_scale) for _ in range(input_size)]
        for _ in range(hidden_size)
    ]
    hidden_output = [
        [rng.uniform(-output_scale, output_scale) for _ in range(hidden_size)]
        for _ in range(output_size)
    ]
    return input_hidden, [0.0] * hidden_size, hidden_output, [0.0] * output_size


def _feature_stats(
    rows: list[dict[str, float]],
    feature_names: tuple[str, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if not rows:
        return tuple(0.0 for _ in feature_names), tuple(1.0 for _ in feature_names)
    means: list[float] = []
    scales: list[float] = []
    for name in feature_names:
        values = [float(row.get(name, 0.0) or 0.0) for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean)
        scales.append(max(math.sqrt(variance), 1e-6))
    return tuple(means), tuple(scales)


def _normalize(
    values: list[float],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> list[float]:
    return [
        (float(value) - means[index]) / max(scales[index], 1e-6)
        for index, value in enumerate(values)
    ]


def _hidden_activation(
    input_hidden_weights: tuple[tuple[float, ...], ...],
    hidden_bias: tuple[float, ...],
    vector: list[float],
) -> list[float]:
    return [
        math.tanh(sum(weight * value for weight, value in zip(row, vector, strict=False)) + bias)
        for row, bias in zip(input_hidden_weights, hidden_bias, strict=False)
    ]


def _output_logits(
    hidden_output_weights: tuple[tuple[float, ...], ...],
    output_bias: tuple[float, ...],
    hidden: list[float],
) -> list[float]:
    return [
        sum(weight * value for weight, value in zip(row, hidden, strict=False)) + bias
        for row, bias in zip(hidden_output_weights, output_bias, strict=False)
    ]


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    max_logit = max(logits)
    exps = [math.exp(max(min(value - max_logit, 50.0), -50.0)) for value in logits]
    total = sum(exps) or 1.0
    return [value / total for value in exps]


def _per_label_metrics(confusion: dict[str, dict[str, int]], labels: tuple[str, ...]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = confusion.get(label, {}).get(label, 0)
        fp = sum(preds.get(label, 0) for truth, preds in confusion.items() if truth != label)
        fn = sum(count for pred, count in confusion.get(label, {}).items() if pred != label)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        result[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": int(tp + fn),
        }
    return result


def _round2(values: list[list[float]]) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(round(value, 10) for value in row) for row in values)


def _tuple2(value: object) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(tuple(float(item) for item in row) for row in value if isinstance(row, (list, tuple)))


def _char_rate(text: str, chars: set[str]) -> float:
    compact = "".join(text.split())
    if not compact:
        return 0.0
    return sum(1 for char in compact if char in chars) / len(compact)


def _operator_rate(text: str) -> float:
    operators = set("+-=*/<>|()[]{}.,:;^_")
    math_chars = set("−×÷≤≥≈≠∈∉∑∏∫√∞αβγδθλμσφψωΓΔΘΛΠΣΦΨΩ")
    compact = "".join(text.split())
    if not compact:
        return 0.0
    return sum(1 for char in compact if char in operators or char in math_chars) / len(compact)


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
