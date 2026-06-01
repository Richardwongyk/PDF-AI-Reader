"""Score TinyBDMath graph rows with an edge relation model.

This module turns a saved edge baseline into candidate relation evidence.  It
does not decode final LaTeX and never marks formulas accepted; it prepares the
auditable relation layer that r2a/fusion/verifier can consume.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any

from src.core.tinybdmath_edge_baseline import (
    EDGE_CONTEXT_FEATURES,
    TinyBDEdgeBaselineModel,
    add_graph_context_features,
    edge_features,
)


RELATION_SCORE_SCHEMA_VERSION = "tinybdmath_relation_scores_v1"


@dataclass(frozen=True)
class TinyBDRelationScore:
    edge_id: str
    source: str
    target: str
    hint: str
    predicted_relation: str
    confidence: float
    probabilities: dict[str, float]
    features: dict[str, float]


@dataclass(frozen=True)
class TinyBDScoredGraph:
    schema_version: str
    row_id: str
    case: str
    kind: str
    page_num: int | None
    label_latex: str
    model_version: str
    graph_input_hash: str
    relation_scores: tuple[TinyBDRelationScore, ...]
    relation_summary: dict[str, Any]
    verifier_warnings: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return _json_compatible(asdict(self))


class TinyBDRelationScorer:
    def __init__(
        self,
        model: TinyBDEdgeBaselineModel,
        *,
        min_confidence: float = 0.55,
        max_edges: int = 256,
    ) -> None:
        self.model = model
        self.min_confidence = float(min_confidence)
        self.max_edges = int(max_edges)

    @classmethod
    def from_model_path(
        cls,
        path: Path,
        *,
        min_confidence: float = 0.55,
        max_edges: int = 256,
    ) -> "TinyBDRelationScorer":
        return cls(TinyBDEdgeBaselineModel.load(path), min_confidence=min_confidence, max_edges=max_edges)

    def score_graph_row(self, row: dict[str, Any]) -> TinyBDScoredGraph:
        scores: list[TinyBDRelationScore] = []
        raw_edges = [edge for edge in row.get("candidate_edges", []) if isinstance(edge, dict)]
        for edge in add_graph_context_features(raw_edges)[: self.max_edges]:
            if not isinstance(edge, dict):
                continue
            probabilities = self.model.predict_proba(edge)
            predicted, confidence = _best(probabilities)
            if confidence < self.min_confidence:
                predicted = "LOW_CONFIDENCE"
            scores.append(
                TinyBDRelationScore(
                    edge_id=str(edge.get("edge_id", "")),
                    source=str(edge.get("source", "")),
                    target=str(edge.get("target", "")),
                    hint=str(edge.get("hint", "")),
                    predicted_relation=predicted,
                    confidence=round(confidence, 6),
                    probabilities=probabilities,
                    features=_numeric_features(edge.get("features", {})),
                )
            )
        warnings = _verifier_warnings(row, scores)
        return TinyBDScoredGraph(
            schema_version=RELATION_SCORE_SCHEMA_VERSION,
            row_id=str(row.get("row_id", "")),
            case=str(row.get("case", "")),
            kind=str(row.get("kind", "")),
            page_num=_optional_int(row.get("page_num")),
            label_latex=str(row.get("label_latex", "")),
            model_version=self.model.version,
            graph_input_hash=str(row.get("input_hash", "")),
            relation_scores=tuple(scores),
            relation_summary=_summary(scores),
            verifier_warnings=tuple(warnings),
        )


def score_rows(
    rows: list[dict[str, Any]],
    model: TinyBDEdgeBaselineModel,
    *,
    min_confidence: float = 0.55,
    max_edges: int = 256,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scorer = TinyBDRelationScorer(model, min_confidence=min_confidence, max_edges=max_edges)
    scored = [scorer.score_graph_row(row).to_json() for row in rows]
    warnings = Counter(warning for row in scored for warning in row.get("verifier_warnings", []))
    relations = Counter(
        score.get("predicted_relation", "")
        for row in scored
        for score in row.get("relation_scores", [])
    )
    manifest = {
        "schema_version": "tinybdmath_relation_score_manifest_v2_vector_rule_radical",
        "model_version": model.version,
        "rows": len(scored),
        "relation_scores": sum(len(row.get("relation_scores", [])) for row in scored),
        "relation_counts": dict(sorted(relations.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "min_confidence": min_confidence,
        "max_edges": max_edges,
        "candidate_only": True,
    }
    return scored, manifest


def score_rows_torch(
    rows: list[dict[str, Any]],
    model: TinyBDEdgeBaselineModel,
    *,
    min_confidence: float = 0.55,
    max_edges: int = 256,
    batch_rows: int = 512,
    device: str = "cpu",
    compact_output: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    predictor = _TorchEdgeBatchPredictor(model, device=device)
    scored: list[dict[str, Any]] = []
    for offset in range(0, len(rows), max(1, int(batch_rows))):
        scored.extend(
            _score_row_batch_torch(
                rows[offset : offset + max(1, int(batch_rows))],
                model,
                predictor,
                min_confidence=min_confidence,
                max_edges=max_edges,
                compact_output=compact_output,
            )
        )
    warnings = Counter(warning for row in scored for warning in row.get("verifier_warnings", []))
    relations = Counter(
        score.get("predicted_relation", "")
        for row in scored
        for score in row.get("relation_scores", [])
    )
    manifest = {
        "schema_version": "tinybdmath_relation_score_manifest_v2_vector_rule_radical",
        "model_version": model.version,
        "rows": len(scored),
        "relation_scores": sum(len(row.get("relation_scores", [])) for row in scored),
        "relation_counts": dict(sorted(relations.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "min_confidence": min_confidence,
        "max_edges": max_edges,
        "candidate_only": True,
        "torch_batched": True,
        "torch_device": str(device),
        "batch_rows": max(1, int(batch_rows)),
        "compact_output": bool(compact_output),
    }
    return scored, manifest


def score_jsonl_stream(
    rows_path: Path,
    model: TinyBDEdgeBaselineModel,
    output_dir: Path,
    *,
    limit: int = 0,
    min_confidence: float = 0.55,
    max_edges: int = 256,
) -> dict[str, Any]:
    """Score graph rows incrementally so large training sets do not wait for all rows in memory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    scorer = TinyBDRelationScorer(model, min_confidence=min_confidence, max_edges=max_edges)
    warnings: Counter[str] = Counter()
    relations: Counter[str] = Counter()
    row_count = 0
    score_count = 0
    output_path = output_dir / "tinybdmath_relation_scores.jsonl"
    with rows_path.open("r", encoding="utf-8") as source, output_path.open("w", encoding="utf-8") as sink:
        for line in source:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                continue
            row = scorer.score_graph_row(value).to_json()
            sink.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            row_count += 1
            warnings.update(row.get("verifier_warnings", []))
            for score in row.get("relation_scores", []):
                relations.update([str(score.get("predicted_relation", ""))])
            score_count += len(row.get("relation_scores", []))
            if limit > 0 and row_count >= limit:
                break
    manifest = {
        "schema_version": "tinybdmath_relation_score_manifest_v2_vector_rule_radical",
        "model_version": model.version,
        "rows": row_count,
        "relation_scores": score_count,
        "relation_counts": dict(sorted(relations.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "min_confidence": min_confidence,
        "max_edges": max_edges,
        "candidate_only": True,
        "streaming": True,
    }
    (output_dir / "tinybdmath_relation_score_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def score_jsonl_stream_torch(
    rows_path: Path,
    model: TinyBDEdgeBaselineModel,
    output_dir: Path,
    *,
    limit: int = 0,
    min_confidence: float = 0.55,
    max_edges: int = 256,
    batch_rows: int = 512,
    device: str = "cpu",
    compact_output: bool = False,
    scored_batch_callback: Any | None = None,
    write_scores: bool = True,
) -> dict[str, Any]:
    """Score graph rows incrementally with PyTorch batched tensor inference."""

    output_dir.mkdir(parents=True, exist_ok=True)
    predictor = _TorchEdgeBatchPredictor(model, device=device)
    warnings: Counter[str] = Counter()
    relations: Counter[str] = Counter()
    row_count = 0
    score_count = 0
    row_limit = int(limit or 0)
    chunk_size = max(1, int(batch_rows or 512))
    output_path = output_dir / "tinybdmath_relation_scores.jsonl"

    def flush(batch: list[dict[str, Any]], sink: Any | None) -> None:
        nonlocal row_count, score_count
        scored_batch = _score_row_batch_torch(
            batch,
            model,
            predictor,
            min_confidence=min_confidence,
            max_edges=max_edges,
            compact_output=compact_output,
        )
        if scored_batch_callback is not None:
            scored_batch_callback(scored_batch)
        for row in scored_batch:
            if sink is not None:
                sink.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            row_count += 1
            warnings.update(row.get("verifier_warnings", []))
            for score in row.get("relation_scores", []):
                relations.update([str(score.get("predicted_relation", ""))])
            score_count += len(row.get("relation_scores", []))

    sink_context = output_path.open("w", encoding="utf-8") if write_scores else None
    with rows_path.open("r", encoding="utf-8") as source:
        batch: list[dict[str, Any]] = []
        try:
            for line in source:
                if row_limit > 0 and row_count + len(batch) >= row_limit:
                    break
                text = line.strip()
                if not text:
                    continue
                value = json.loads(text)
                if not isinstance(value, dict):
                    continue
                batch.append(value)
                if len(batch) >= chunk_size:
                    flush(batch, sink_context)
                    batch = []
            if batch:
                flush(batch, sink_context)
        finally:
            if sink_context is not None:
                sink_context.close()
    manifest = {
        "schema_version": "tinybdmath_relation_score_manifest_v2_vector_rule_radical",
        "model_version": model.version,
        "rows": row_count,
        "relation_scores": score_count,
        "relation_counts": dict(sorted(relations.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "min_confidence": min_confidence,
        "max_edges": max_edges,
        "candidate_only": True,
        "streaming": True,
        "torch_batched": True,
        "torch_device": str(device),
        "batch_rows": chunk_size,
        "compact_output": bool(compact_output),
        "score_jsonl_written": bool(write_scores),
    }
    (output_dir / "tinybdmath_relation_score_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


class _TorchEdgeBatchPredictor:
    def __init__(self, model: TinyBDEdgeBaselineModel, *, device: str = "cpu") -> None:
        try:
            import torch
        except Exception as exc:  # pragma: no cover - depends on optional runtime.
            raise RuntimeError("PyTorch is required for fast TinyBDMath relation scoring") from exc

        self._torch = torch
        self._device = torch.device(device)
        self._labels = tuple(model.labels)
        self._feature_names = tuple(model.feature_names)
        width = len(self._feature_names)
        self._means = torch.tensor(
            [model.means[index] if index < len(model.means) else 0.0 for index in range(width)],
            dtype=torch.float64,
            device=self._device,
        )
        self._scales = torch.tensor(
            [
                max(model.scales[index] if index < len(model.scales) else 1.0, 1e-6)
                for index in range(width)
            ],
            dtype=torch.float64,
            device=self._device,
        )
        self._model_type = model.model_type
        self._hidden_weights = [
            torch.tensor(layer, dtype=torch.float64, device=self._device)
            for layer in model.hidden_weights
        ]
        self._hidden_biases = [
            torch.tensor(layer, dtype=torch.float64, device=self._device)
            for layer in model.hidden_biases
        ]
        output_weights = model.output_weights or model.weights
        self._output_weights = torch.tensor(output_weights, dtype=torch.float64, device=self._device)
        self._output_bias = torch.tensor(
            model.output_bias if model.output_bias else [0.0 for _ in self._labels],
            dtype=torch.float64,
            device=self._device,
        )
        self._linear_weights = torch.tensor(model.weights, dtype=torch.float64, device=self._device)

    @property
    def labels(self) -> tuple[str, ...]:
        return self._labels

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self._feature_names

    def predict_batch(
        self,
        edges: list[dict[str, Any]],
        *,
        include_probabilities: bool = True,
    ) -> tuple[list[int], list[float], list[dict[str, float]]]:
        if not edges:
            return [], [], []
        vectors = [_edge_feature_vector(edge, self._feature_names) for edge in edges]
        return self.predict_vectors(vectors, include_probabilities=include_probabilities)

    def predict_vectors(
        self,
        vectors: list[list[float]],
        *,
        include_probabilities: bool = True,
    ) -> tuple[list[int], list[float], list[dict[str, float]]]:
        if not vectors:
            return [], [], []
        torch = self._torch
        with torch.inference_mode():
            activations = torch.tensor(vectors, dtype=torch.float64, device=self._device)
            activations = (activations - self._means) / self._scales
            if self._model_type == "mlp_relu" or self._hidden_weights:
                for layer_index, weights in enumerate(self._hidden_weights):
                    bias = (
                        self._hidden_biases[layer_index]
                        if layer_index < len(self._hidden_biases)
                        else torch.zeros(weights.shape[0], dtype=torch.float64, device=self._device)
                    )
                    activations = torch.relu(activations.matmul(weights.t()) + bias)
                logits = activations.matmul(self._output_weights.t()) + self._output_bias
            else:
                logits = activations.matmul(self._linear_weights.t())
            probabilities_tensor = torch.softmax(logits, dim=1)
            confidences_tensor, indices_tensor = torch.max(probabilities_tensor, dim=1)
            indices = [int(value) for value in indices_tensor.detach().cpu().tolist()]
            confidences = [round(float(value), 6) for value in confidences_tensor.detach().cpu().tolist()]
            if include_probabilities:
                probabilities_rows = probabilities_tensor.detach().cpu().tolist()
            else:
                probabilities_rows = []
        probabilities = (
            [
                {label: round(float(row[index]), 6) for index, label in enumerate(self._labels)}
                for row in probabilities_rows
            ]
            if include_probabilities
            else []
        )
        return indices, confidences, probabilities


def _score_row_batch_torch(
    rows: list[dict[str, Any]],
    model: TinyBDEdgeBaselineModel,
    predictor: _TorchEdgeBatchPredictor,
    *,
    min_confidence: float,
    max_edges: int,
    compact_output: bool,
) -> list[dict[str, Any]]:
    row_edges, feature_vectors = _prepare_torch_scoring_batch(
        rows,
        feature_names=predictor.feature_names,
        max_edges=max_edges,
    )
    predicted_indices, confidences, flat_probabilities = predictor.predict_vectors(
        feature_vectors,
        include_probabilities=not compact_output,
    )
    output: list[dict[str, Any]] = []
    offset = 0
    for row, edges in zip(rows, row_edges):
        scores: list[dict[str, Any]] = []
        for edge in edges:
            label_index = predicted_indices[offset]
            confidence = confidences[offset]
            probabilities = flat_probabilities[offset] if not compact_output else {}
            offset += 1
            predicted = predictor.labels[label_index] if label_index < len(predictor.labels) else "LOW_CONFIDENCE"
            if confidence < min_confidence:
                predicted = "LOW_CONFIDENCE"
            score = {
                "edge_id": str(edge.get("edge_id", "")),
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "hint": str(edge.get("hint", "")),
                "predicted_relation": predicted,
                "confidence": round(confidence, 6),
                "features": _compact_features(edge) if compact_output else _numeric_features(edge.get("features", {})),
            }
            if not compact_output:
                score["probabilities"] = probabilities
            scores.append(score)
        warnings = _verifier_warnings_from_score_dicts(row, scores)
        output.append(
            {
                "schema_version": RELATION_SCORE_SCHEMA_VERSION,
                "row_id": str(row.get("row_id", "")),
                "case": str(row.get("case", "")),
                "kind": str(row.get("kind", "")),
                "page_num": _optional_int(row.get("page_num")),
                "label_latex": str(row.get("label_latex", "")),
                "model_version": model.version,
                "graph_input_hash": str(row.get("input_hash", "")),
                "relation_scores": scores,
                "relation_summary": _summary_from_score_dicts(scores),
                "verifier_warnings": list(warnings),
            }
        )
    return output


def _edge_feature_vector(edge: dict[str, Any], feature_names: tuple[str, ...]) -> list[float]:
    features = edge.get("features", {})
    if not isinstance(features, dict):
        features = {}
    context = edge.get("context_features", {})
    if not isinstance(context, dict):
        context = {}
    hint = str(edge.get("hint", ""))
    return [_feature_value(name, features, context, hint) for name in feature_names]


def _prepare_torch_scoring_batch(
    rows: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    max_edges: int,
) -> tuple[list[list[dict[str, Any]]], list[list[float]]]:
    row_edges: list[list[dict[str, Any]]] = []
    feature_vectors: list[list[float]] = []
    for row in rows:
        raw_edges = [edge for edge in row.get("candidate_edges", []) if isinstance(edge, dict)]
        edge_infos, vectors = _prepare_row_torch_features(
            raw_edges,
            feature_names=feature_names,
            max_edges=max_edges,
        )
        row_edges.append(edge_infos)
        feature_vectors.extend(vectors)
    return row_edges, feature_vectors


def _prepare_row_torch_features(
    raw_edges: list[dict[str, Any]],
    *,
    feature_names: tuple[str, ...],
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[list[float]]]:
    if not raw_edges:
        return [], []
    edges = [edge for edge in raw_edges if isinstance(edge, dict)]
    count = len(edges)
    sources = [str(edge.get("source", "")) for edge in edges]
    targets = [str(edge.get("target", "")) for edge in edges]
    hints = [str(edge.get("hint", "")) for edge in edges]
    families = [_hint_family(hint) for hint in hints]
    features = [edge.get("features", {}) if isinstance(edge.get("features", {}), dict) else {} for edge in edges]
    distances = [
        abs(_float(feature.get("dx_over_height"))) + abs(_float(feature.get("dy_over_height")))
        for feature in features
    ]
    edge_ids = [str(edge.get("edge_id", "")) for edge in edges]

    source_indices: dict[str, list[int]] = {}
    target_indices: dict[str, list[int]] = {}
    source_family_indices: dict[tuple[str, str], list[int]] = {}
    target_family_indices: dict[tuple[str, str], list[int]] = {}
    for index, (source, target, family) in enumerate(zip(sources, targets, families)):
        source_indices.setdefault(source, []).append(index)
        target_indices.setdefault(target, []).append(index)
        source_family_indices.setdefault((source, family), []).append(index)
        target_family_indices.setdefault((target, family), []).append(index)

    source_count = [_log_count(len(source_indices[source])) for source in sources]
    target_count = [_log_count(len(target_indices[target])) for target in targets]
    source_same_count = [_log_count(len(source_family_indices[(source, family)])) for source, family in zip(sources, families)]
    target_same_count = [_log_count(len(target_family_indices[(target, family)])) for target, family in zip(targets, families)]
    source_rank = [0.0 for _ in range(count)]
    target_rank = [0.0 for _ in range(count)]
    source_margin = [0.0 for _ in range(count)]
    target_margin = [0.0 for _ in range(count)]
    _fill_rank_and_margin(source_family_indices.values(), distances, edge_ids, source_rank, source_margin)
    _fill_rank_and_margin(target_family_indices.values(), distances, edge_ids, target_rank, target_margin)

    source_flags = {
        source: {
            "source_has_fraction_bar": any(hints[index] == "fraction_bar_candidate" for index in indices),
            "source_has_above_candidate": any(hints[index] in {"above_zone", "above_rule_candidate"} for index in indices),
            "source_has_below_candidate": any(hints[index] in {"below_zone", "below_rule_candidate"} for index in indices),
            "source_has_script_child_candidate": any(hints[index] in {"superscript_zone", "subscript_zone"} for index in indices),
        }
        for source, indices in source_indices.items()
    }
    target_has_script_parent = {
        target: any(hints[index] in {"superscript_zone", "subscript_zone"} for index in indices)
        for target, indices in target_indices.items()
    }

    edge_limit = min(count, int(max_edges))
    edge_infos: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    for index in range(edge_limit):
        context = {
            "source_candidate_count": source_count[index],
            "target_candidate_count": target_count[index],
            "source_same_relation_count": source_same_count[index],
            "target_same_relation_count": target_same_count[index],
            "source_relation_rank": source_rank[index],
            "target_relation_rank": target_rank[index],
            "source_best_relation_margin": source_margin[index],
            "target_best_relation_margin": target_margin[index],
            "source_has_fraction_bar": 1.0 if source_flags[sources[index]]["source_has_fraction_bar"] else 0.0,
            "source_has_above_candidate": 1.0 if source_flags[sources[index]]["source_has_above_candidate"] else 0.0,
            "source_has_below_candidate": 1.0 if source_flags[sources[index]]["source_has_below_candidate"] else 0.0,
            "target_has_script_parent_candidate": 1.0 if target_has_script_parent[targets[index]] else 0.0,
            "source_has_script_child_candidate": 1.0 if source_flags[sources[index]]["source_has_script_child_candidate"] else 0.0,
        }
        edge_infos.append(edges[index])
        vectors.append(
            [
                _feature_value(name, features[index], context, hints[index])
                for name in feature_names
            ]
        )
    return edge_infos, vectors


def _fill_rank_and_margin(
    groups: Any,
    distances: list[float],
    edge_ids: list[str],
    rank_output: list[float],
    margin_output: list[float],
) -> None:
    for indices in groups:
        if not indices:
            continue
        ordered = sorted(indices, key=lambda index: (distances[index], edge_ids[index]))
        denominator = max(1, len(ordered) - 1)
        best = distances[ordered[0]]
        for rank, index in enumerate(ordered):
            rank_output[index] = rank / denominator
            margin_output[index] = max(0.0, min(4.0, distances[index] - best))


def _hint_family(hint: str) -> str:
    if hint in {"above_zone", "above_rule_candidate"}:
        return "above"
    if hint in {"below_zone", "below_rule_candidate"}:
        return "below"
    if hint in {"superscript_zone", "subscript_zone", "right_neighbor", "far_context", "fraction_bar_candidate", "overline_candidate", "radical_body_candidate"}:
        return hint
    if "rule" in hint:
        return "rule"
    return hint or "unknown"


def _log_count(value: int) -> float:
    return math.log1p(max(0, int(value)))


def _feature_value(name: str, features: dict[str, Any], context: dict[str, Any], hint: str) -> float:
    if name == "bias":
        return 1.0
    if name in EDGE_CONTEXT_FEATURES:
        return _float(context.get(name))
    if name == "hint_right_neighbor":
        return 1.0 if hint == "right_neighbor" else 0.0
    if name == "hint_superscript_zone":
        return 1.0 if hint == "superscript_zone" else 0.0
    if name == "hint_subscript_zone":
        return 1.0 if hint == "subscript_zone" else 0.0
    if name == "hint_above_zone":
        return 1.0 if hint in {"above_zone", "above_rule_candidate"} else 0.0
    if name == "hint_below_zone":
        return 1.0 if hint in {"below_zone", "below_rule_candidate"} else 0.0
    if name == "hint_far_context":
        return 1.0 if hint == "far_context" else 0.0
    if name == "hint_rule":
        return 1.0 if "rule" in hint or "fraction_bar" in hint else 0.0
    if name == "hint_above_rule":
        return 1.0 if hint == "above_rule_candidate" else 0.0
    if name == "hint_below_rule":
        return 1.0 if hint == "below_rule_candidate" else 0.0
    if name == "hint_fraction_bar":
        return 1.0 if hint == "fraction_bar_candidate" else 0.0
    if name == "hint_overline":
        return 1.0 if hint == "overline_candidate" else 0.0
    if name == "hint_radical_body":
        return 1.0 if hint == "radical_body_candidate" else 0.0
    return _float(features.get(name))


def _compact_features(edge: dict[str, Any]) -> dict[str, float]:
    features = edge.get("features", {})
    if not isinstance(features, dict):
        return {}
    return {
        "dx_over_height": _float(features.get("dx_over_height")),
        "dy_over_height": _float(features.get("dy_over_height")),
    }


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _summary(scores: list[TinyBDRelationScore]) -> dict[str, Any]:
    counts = Counter(score.predicted_relation for score in scores)
    strong = sum(1 for score in scores if score.confidence >= 0.80 and score.predicted_relation != "LOW_CONFIDENCE")
    return {
        "edge_count": len(scores),
        "relation_counts": dict(sorted(counts.items())),
        "strong_relation_edges": strong,
        "average_confidence": round(sum(score.confidence for score in scores) / len(scores), 6) if scores else 0.0,
    }


def _summary_from_score_dicts(scores: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(score.get("predicted_relation", "")) for score in scores)
    strong = sum(
        1
        for score in scores
        if float(score.get("confidence", 0.0) or 0.0) >= 0.80
        and str(score.get("predicted_relation", "")) != "LOW_CONFIDENCE"
    )
    return {
        "edge_count": len(scores),
        "relation_counts": dict(sorted(counts.items())),
        "strong_relation_edges": strong,
        "average_confidence": round(
            sum(float(score.get("confidence", 0.0) or 0.0) for score in scores) / len(scores),
            6,
        ) if scores else 0.0,
    }


def _verifier_warnings(row: dict[str, Any], scores: list[TinyBDRelationScore]) -> list[str]:
    return _verifier_warnings_from_counts(
        row,
        Counter(score.predicted_relation for score in scores),
        len(scores),
    )


def _verifier_warnings_from_score_dicts(row: dict[str, Any], scores: list[dict[str, Any]]) -> list[str]:
    return _verifier_warnings_from_counts(
        row,
        Counter(str(score.get("predicted_relation", "")) for score in scores),
        len(scores),
    )


def _verifier_warnings_from_counts(row: dict[str, Any], predicted: Counter[str], score_count: int) -> list[str]:
    warnings: list[str] = []
    tags = set(str(tag) for tag in row.get("coverage_tags", []))
    if score_count <= 0:
        warnings.append("no_relation_scores")
    if predicted.get("LOW_CONFIDENCE", 0) > max(3, score_count // 3):
        warnings.append("many_low_confidence_edges")
    if "subscript" in tags and predicted.get("SUB", 0) == 0:
        warnings.append("expected_subscript_not_scored")
    if "superscript" in tags and predicted.get("SUP", 0) == 0:
        warnings.append("expected_superscript_not_scored")
    if "fraction" in tags and predicted.get("FRACTION_BAR", 0) == 0:
        warnings.append("expected_fraction_bar_not_scored")
    if "single_glyph_or_empty_text" in tags and score_count > 8:
        warnings.append("single_glyph_has_many_edges")
    return sorted(set(warnings))


def _best(probabilities: dict[str, float]) -> tuple[str, float]:
    if not probabilities:
        return "LOW_CONFIDENCE", 0.0
    label, confidence = max(probabilities.items(), key=lambda item: item[1])
    return label, float(confidence)


def _numeric_features(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            result[str(key)] = float(item or 0.0)
        except (TypeError, ValueError):
            continue
    return result


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_compatible(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    return value


def read_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def write_scored_rows(rows: list[dict[str, Any]], manifest: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "tinybdmath_relation_scores.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_dir / "tinybdmath_relation_score_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
