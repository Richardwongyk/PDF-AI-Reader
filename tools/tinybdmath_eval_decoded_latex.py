from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_ALTERNATIVE_CONFIDENCE_FLOOR,
    GRAPH_PARSER_ALTERNATIVE_THRESHOLD_FACTOR,
    GRAPH_PARSER_RUNTIME_RELATION_CONFIDENCE_FLOOR,
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
    candidate_pairs,
    graph_parser_predictions_to_structural_candidate,
    graph_nodes,
    graph_parser_features,
    graph_parser_graph_feature_map,
    graph_parser_node_features,
    graph_parser_structured_relation_selection,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate TinyBDMath decoded LaTeX candidates against graph-row labels.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    candidate_source = parser.add_mutually_exclusive_group(required=True)
    candidate_source.add_argument("--candidates", type=Path, help="Graph Parser structural candidate JSONL")
    candidate_source.add_argument(
        "--graph-parser-model",
        type=Path,
        help="Graph Parser JSON artifact; candidates are generated directly from graph rows.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--candidates-output",
        type=Path,
        help="Write generated Graph Parser structural candidates and exit before decoded LaTeX evaluation.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Evaluate rows incrementally by joining graph rows and candidates in file order.",
    )
    parser.add_argument(
        "--torch-inference",
        action="store_true",
        help="Deprecated compatibility flag; Graph Parser model evaluation uses batched PyTorch inference by default.",
    )
    parser.add_argument(
        "--python-inference",
        action="store_true",
        help="Use the legacy pure-Python Graph Parser inference path. Intended only for no-torch smoke checks.",
    )
    parser.add_argument("--torch-batch-size", type=int, default=65536)
    parser.add_argument("--relation-threshold", type=float, default=None)
    parser.add_argument("--keep-threshold", type=float, default=None)
    parser.add_argument(
        "--full-verifier",
        action="store_true",
        help="Run slower layout verifier and n-best audit during decoded evaluation.",
    )
    args = parser.parse_args()

    if args.stream and args.graph_parser_model is not None:
        parser.error("--stream is only supported with --candidates")
    if args.output is None and args.candidates_output is None:
        parser.error("--output is required unless --candidates-output is used")
    if args.stream:
        decoded_rows, warnings = _decode_rows_stream(
            args.graph_rows,
            args.candidates,
            limit=args.limit,
            full_verifier=bool(args.full_verifier),
        )
    else:
        graph_rows = _read_jsonl(args.graph_rows, limit=args.limit)
        if args.graph_parser_model is not None:
            if args.python_inference:
                candidates = _candidates_from_graph_parser(graph_rows, args.graph_parser_model)
            else:
                candidates = _candidates_from_graph_parser_torch(
                    graph_rows,
                    args.graph_parser_model,
                    batch_size=int(args.torch_batch_size),
                    relation_threshold=args.relation_threshold,
                    keep_threshold=args.keep_threshold,
                )
            if args.candidates_output is not None:
                _write_jsonl(args.candidates_output, candidates)
                print(
                    json.dumps(
                        {
                            "schema_version": "tinybdmath_graph_parser_candidates_export_v1",
                            "rows": len(candidates),
                            "torch_inference": not bool(args.python_inference),
                            "output": str(args.candidates_output),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
        else:
            candidates = _read_jsonl(args.candidates, limit=args.limit)
        rows_by_id = {str(row.get("row_id", "")): row for row in graph_rows}
        decoded_rows, warnings = _decode_rows(candidates, rows_by_id, full_verifier=bool(args.full_verifier))
    report = _build_report(decoded_rows, warnings, streaming=bool(args.stream))
    assert args.output is not None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    rows_path = args.output.with_suffix(".jsonl")
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in decoded_rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"schema_version": report["schema_version"], "rows": report["rows"], **report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


def _candidates_from_graph_parser(
    graph_rows: list[dict[str, Any]],
    graph_parser_model: Path,
) -> list[dict[str, Any]]:
    parser = TinyBDGraphParser.load(graph_parser_model)
    candidates: list[dict[str, Any]] = []
    for graph_row in graph_rows:
        predictions = parser.predict_row(graph_row)
        structural = graph_parser_predictions_to_structural_candidate(predictions)
        structural["row_id"] = str(graph_row.get("row_id", "") or "")
        structural["kind"] = str(graph_row.get("kind", "") or "")
        candidates.append(structural)
    return candidates


def _candidates_from_graph_parser_torch(
    graph_rows: list[dict[str, Any]],
    graph_parser_model: Path,
    *,
    batch_size: int,
    relation_threshold: float | None = None,
    keep_threshold: float | None = None,
) -> list[dict[str, Any]]:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on optional training env
        raise SystemExit(f"torch_unavailable: {exc}") from exc
    predictor = _TorchGraphParserPredictor(
        TinyBDGraphParserArtifact.load(graph_parser_model),
        torch=torch,
        batch_size=max(1, int(batch_size)),
        relation_threshold=relation_threshold,
        keep_threshold=keep_threshold,
    )
    candidates: list[dict[str, Any]] = []
    for graph_row, predictions in zip(graph_rows, predictor.predict_rows(graph_rows)):
        structural = graph_parser_predictions_to_structural_candidate(predictions)
        structural["row_id"] = str(graph_row.get("row_id", "") or "")
        structural["kind"] = str(graph_row.get("kind", "") or "")
        candidates.append(structural)
    return candidates


class _TorchGraphParserPredictor:
    def __init__(
        self,
        artifact: TinyBDGraphParserArtifact,
        *,
        torch: Any,
        batch_size: int,
        relation_threshold: float | None = None,
        keep_threshold: float | None = None,
    ) -> None:
        self.artifact = artifact
        self.torch = torch
        self.batch_size = max(1, int(batch_size))
        self.edge_hidden_layers = _torch_hidden_layers(
            hidden_weights=artifact.hidden_weights,
            hidden_biases=artifact.hidden_biases,
            torch=torch,
        )
        self.node_hidden_layers = _torch_hidden_layers(
            hidden_weights=artifact.node_hidden_weights,
            hidden_biases=artifact.node_hidden_biases,
            torch=torch,
        )
        self.graph_hidden_layers = _torch_hidden_layers(
            hidden_weights=artifact.graph_hidden_weights,
            hidden_biases=artifact.graph_hidden_biases,
            torch=torch,
        )
        self.output_weight = torch.tensor(artifact.output_weights, dtype=torch.float32) if artifact.output_weights else None
        self.output_bias = torch.tensor(artifact.output_bias, dtype=torch.float32) if artifact.output_bias else None
        self.node_output_weight = torch.tensor(artifact.node_output_weights, dtype=torch.float32) if artifact.node_output_weights else None
        self.node_output_bias = torch.tensor(artifact.node_output_bias, dtype=torch.float32) if artifact.node_output_bias else None
        self.keep_output_weight = torch.tensor(artifact.keep_output_weights, dtype=torch.float32) if artifact.keep_output_weights else None
        self.keep_output_bias = torch.tensor([artifact.keep_output_bias], dtype=torch.float32)
        self.relation_means = torch.tensor(artifact.means or [0.0 for _ in artifact.feature_names], dtype=torch.float32)
        self.relation_scales = torch.tensor(artifact.scales or [1.0 for _ in artifact.feature_names], dtype=torch.float32).clamp_min(1e-6)
        self.node_means = torch.tensor(artifact.node_means or [0.0 for _ in artifact.node_feature_names], dtype=torch.float32)
        self.node_scales = torch.tensor(artifact.node_scales or [1.0 for _ in artifact.node_feature_names], dtype=torch.float32).clamp_min(1e-6)
        self.graph_means = torch.tensor(artifact.graph_means or [0.0 for _ in artifact.graph_feature_names], dtype=torch.float32)
        self.graph_scales = torch.tensor(artifact.graph_scales or [1.0 for _ in artifact.graph_feature_names], dtype=torch.float32).clamp_min(1e-6)
        self.mode = str((artifact.train_config or {}).get("mode", "") or "")
        self.model_version = str(artifact.model_version or "")
        self.relation_threshold = float(artifact.threshold if relation_threshold is None else relation_threshold)
        self.keep_threshold = float(artifact.keep_threshold if keep_threshold is None else keep_threshold)

    def predict_row(self, graph_row: dict[str, Any]) -> dict[str, Any]:
        return self.predict_rows([graph_row])[0]

    def predict_rows(self, graph_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        relation_feature_rows: list[list[float]] = []
        source_node_rows: list[list[float]] = []
        target_node_rows: list[list[float]] = []
        graph_feature_rows: list[list[float]] = []
        relation_refs: list[tuple[int, str, str]] = []
        node_feature_rows: list[list[float]] = []
        node_refs: list[tuple[int, str]] = []
        for row_index, graph_row in enumerate(graph_rows):
            all_nodes = graph_nodes(graph_row, include_blank=True)
            nodes = [node for node in all_nodes if node.get("node_type") == "vector" or str(node.get("text", "") or "").strip()]
            pairs = candidate_pairs(nodes)
            node_features_by_id = {
                str(node.get("node_id", "") or ""): graph_parser_node_features(node, nodes)
                for node in nodes
            }
            all_node_features_by_id = {
                str(node.get("node_id", "") or ""): graph_parser_node_features(node, all_nodes)
                for node in all_nodes
            }
            graph_features_by_pair = graph_parser_graph_feature_map(nodes, pairs)
            prepared.append(
                {
                    "graph_row": graph_row,
                    "nodes": nodes,
                    "pairs": pairs,
                    "predictions": [],
                    "relation_alternatives": [],
                    "selected_confidences": [],
                    "node_predictions": [],
                }
            )
            for source, target in pairs:
                source_id = str(source.get("node_id", "") or "")
                target_id = str(target.get("node_id", "") or "")
                edge_features = graph_parser_features(source, target, nodes)
                relation_feature_rows.append(
                    [float(edge_features.get(name, 0.0) or 0.0) for name in self.artifact.feature_names]
                )
                source_node_rows.append(
                    [float(node_features_by_id.get(source_id, {}).get(name, 0.0) or 0.0) for name in self.artifact.node_feature_names]
                )
                target_node_rows.append(
                    [float(node_features_by_id.get(target_id, {}).get(name, 0.0) or 0.0) for name in self.artifact.node_feature_names]
                )
                graph_features = graph_features_by_pair.get((source_id, target_id), {})
                graph_feature_rows.append(
                    [float(graph_features.get(name, 0.0) or 0.0) for name in self.artifact.graph_feature_names]
                )
                relation_refs.append((row_index, source_id, target_id))
            for node in all_nodes:
                node_id = str(node.get("node_id", "") or "")
                node_feature_rows.append(
                    [float(all_node_features_by_id.get(node_id, {}).get(name, 0.0) or 0.0) for name in self.artifact.node_feature_names]
                )
                node_refs.append((row_index, node_id))
        if relation_feature_rows:
            probabilities, keep_probabilities = self._predict_relation_outputs(
                relation_feature_rows,
                source_node_rows,
                target_node_rows,
                graph_feature_rows,
            )
            self._attach_relation_predictions(prepared, relation_refs, probabilities, keep_probabilities)
        if node_feature_rows:
            node_probabilities = self._predict_node_probabilities(node_feature_rows)
            self._attach_node_predictions(prepared, node_refs, node_probabilities)
        return [self._prepared_payload(item) for item in prepared]

    def _attach_relation_predictions(
        self,
        prepared: list[dict[str, Any]],
        relation_refs: list[tuple[int, str, str]],
        probabilities: Any,
        keep_probabilities: Any | None,
    ) -> None:
        labels = self.artifact.relation_labels
        cutoff = max(float(self.relation_threshold), GRAPH_PARSER_RUNTIME_RELATION_CONFIDENCE_FLOOR)
        alternative_cutoff = max(GRAPH_PARSER_ALTERNATIVE_CONFIDENCE_FLOOR, cutoff * GRAPH_PARSER_ALTERNATIVE_THRESHOLD_FACTOR)
        keep_cutoff = float(self.keep_threshold or 0.5)
        none_index = labels.index("NONE") if "NONE" in labels else -1
        if keep_probabilities is None or none_index < 0:
            type_confidences, best_indices = probabilities[:, : len(labels)].max(dim=1)
            combined_confidences = type_confidences
            keep_values = None
            alternative_scores = probabilities[:, : len(labels)].clone()
            if none_index >= 0:
                alternative_scores[:, none_index] = -1.0
            selected_mask = (best_indices != none_index) & (type_confidences >= float(cutoff))
        else:
            positive = probabilities[:, : len(labels)].clone()
            positive[:, none_index] = 0.0
            positive_mass = positive.sum(dim=1, keepdim=True).clamp_min(1e-12)
            conditional = positive / positive_mass
            type_confidences, best_indices = conditional.max(dim=1)
            keep_values = keep_probabilities.view(-1)
            combined_confidences = keep_values * type_confidences
            alternative_scores = conditional * keep_values.view(-1, 1)
            alternative_scores[:, none_index] = -1.0
            selected_mask = (
                (best_indices != none_index)
                & (type_confidences >= float(cutoff))
                & (keep_values >= float(keep_cutoff))
            )
        top_count = min(3, len(labels))
        top_values, top_indices = alternative_scores.topk(k=top_count, dim=1)
        best_indices_list = best_indices.tolist()
        type_confidence_list = type_confidences.tolist()
        combined_confidence_list = combined_confidences.tolist()
        selected_list = selected_mask.tolist()
        keep_list = keep_values.tolist() if keep_values is not None else None
        top_values_list = top_values.tolist()
        top_indices_list = top_indices.tolist()
        for index, (row_index, source_id, target_id) in enumerate(relation_refs):
            best_index = int(best_indices_list[index])
            relation = str(labels[best_index]) if 0 <= best_index < len(labels) else "NONE"
            combined_confidence = float(combined_confidence_list[index])
            keep_probability = float(keep_list[index]) if keep_list is not None else None
            alternatives = _top_torch_relation_alternatives_from_indices(
                top_values_list[index],
                top_indices_list[index],
                labels,
            )
            selected = bool(selected_list[index])
            if alternatives and (selected or float(alternatives[0].get("confidence", 0.0) or 0.0) >= alternative_cutoff):
                prepared[row_index]["relation_alternatives"].append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "keep_confidence": round(float(keep_probability), 6) if keep_probability is not None else None,
                        "alternatives": alternatives,
                    }
                )
            if not selected:
                continue
            prepared[row_index]["selected_confidences"].append(combined_confidence)
            prepared[row_index]["predictions"].append(
                {
                    "source": source_id,
                    "target": target_id,
                    "relation": relation,
                    "confidence": round(combined_confidence, 6),
                }
            )

    def _attach_node_predictions(
        self,
        prepared: list[dict[str, Any]],
        node_refs: list[tuple[int, str]],
        probabilities: Any,
    ) -> None:
        labels = self.artifact.node_label_names
        best_values, best_indices = probabilities.max(dim=1)
        best_values_list = best_values.tolist()
        best_indices_list = best_indices.tolist()
        for index, (row_index, node_id) in enumerate(node_refs):
            best_index = int(best_indices_list[index])
            prepared[row_index]["node_predictions"].append(
                {
                    "node_id": node_id,
                    "label": labels[best_index],
                    "confidence": round(float(best_values_list[index]), 6),
                }
            )

    def _prepared_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        predictions = graph_parser_structured_relation_selection(list(item.get("predictions", []) or []))
        selected_confidences = [float(value.get("confidence", 0.0) or 0.0) for value in predictions]
        return {
            "schema_version": "tinybdmath_graph_parser_predictions_v1",
            "model_version": self.artifact.model_version,
            "feature_version": self.artifact.feature_version,
            "input_hash": "",
            "node_count": len(item.get("nodes", []) or []),
            "candidate_pairs": len(item.get("pairs", []) or []),
            "node_predictions": list(item.get("node_predictions", []) or []),
            "node_filter_threshold": float(self.artifact.node_filter_threshold),
            "keep_threshold": float(self.keep_threshold),
            "graph_confidence": round(sum(selected_confidences) / len(selected_confidences), 6) if selected_confidences else 0.0,
            "predictions": predictions,
            "relation_alternatives": sorted(
                list(item.get("relation_alternatives", []) or []),
                key=_torch_relation_alternative_sort_key,
            ),
            "candidate_only": True,
        }

    def _predict_node_labels(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.node_output_weight is None or self.node_output_bias is None:
            return []
        node_features_by_id = {
            str(node.get("node_id", "") or ""): graph_parser_node_features(node, nodes)
            for node in nodes
        }
        feature_rows = [
            [float(node_features_by_id.get(str(node.get("node_id", "") or ""), {}).get(name, 0.0) or 0.0) for name in self.artifact.node_feature_names]
            for node in nodes
        ]
        if not feature_rows:
            return []
        probabilities = self._predict_node_probabilities(feature_rows)
        output: list[dict[str, Any]] = []
        labels = self.artifact.node_label_names
        for index, node in enumerate(nodes):
            row_probabilities = probabilities[index]
            best_index = int(row_probabilities.argmax().item())
            output.append(
                {
                    "node_id": str(node.get("node_id", "") or ""),
                    "label": labels[best_index],
                    "confidence": round(float(row_probabilities[best_index].item()), 6),
                }
            )
        return output

    def _predict_relation_outputs(
        self,
        feature_rows: list[list[float]],
        source_node_rows: list[list[float]],
        target_node_rows: list[list[float]],
        graph_rows: list[list[float]],
    ) -> tuple[Any, Any | None]:
        if not feature_rows or self.output_weight is None or self.output_bias is None:
            return self.torch.empty((0, 0), dtype=self.torch.float32), None
        probability_outputs: list[Any] = []
        keep_outputs: list[Any] = []
        with self.torch.no_grad():
            for start in range(0, len(feature_rows), self.batch_size):
                end = start + self.batch_size
                edge_values = self._normalized_tensor(feature_rows[start:end], means=self.relation_means, scales=self.relation_scales)
                edge_context = _apply_torch_hidden(edge_values, self.edge_hidden_layers, torch=self.torch)
                if not self._uses_context_relation():
                    logits = edge_context.matmul(self.output_weight.t()) + self.output_bias
                    probability_outputs.append(self.torch.softmax(logits, dim=1).cpu())
                    continue
                source_values = self._normalized_tensor(source_node_rows[start:end], means=self.node_means, scales=self.node_scales)
                target_values = self._normalized_tensor(target_node_rows[start:end], means=self.node_means, scales=self.node_scales)
                source_context = _apply_torch_hidden(source_values, self.node_hidden_layers, torch=self.torch)
                target_context = _apply_torch_hidden(target_values, self.node_hidden_layers, torch=self.torch)
                source_logits = source_context.matmul(self.node_output_weight.t()) + self.node_output_bias
                target_logits = target_context.matmul(self.node_output_weight.t()) + self.node_output_bias
                pieces = [edge_context, source_context, target_context]
                if self._uses_graph_context_relation():
                    graph_values = self._normalized_tensor(graph_rows[start:end], means=self.graph_means, scales=self.graph_scales)
                    pieces.append(_apply_torch_hidden(graph_values, self.graph_hidden_layers, torch=self.torch))
                pieces.extend([source_logits, target_logits])
                if self._uses_interaction_relation():
                    pieces.extend(
                        [
                            self.torch.abs(source_context - target_context),
                            source_context * target_context,
                            self.torch.abs(source_logits - target_logits),
                            source_logits * target_logits,
                        ]
                    )
                fused = self.torch.cat(pieces, dim=1)
                logits = fused.matmul(self.output_weight.t()) + self.output_bias
                probability_outputs.append(self.torch.softmax(logits, dim=1).cpu())
                if self._uses_keep_relation() and self.keep_output_weight is not None:
                    keep_outputs.append(self.torch.sigmoid(fused.matmul(self.keep_output_weight) + self.keep_output_bias).cpu())
        keep_probabilities = self.torch.cat(keep_outputs, dim=0).view(-1) if keep_outputs else None
        return self.torch.cat(probability_outputs, dim=0), keep_probabilities

    def _predict_node_probabilities(self, feature_rows: list[list[float]]) -> Any:
        if not feature_rows or self.node_output_weight is None or self.node_output_bias is None:
            return self.torch.empty((0, 0), dtype=self.torch.float32)
        outputs: list[Any] = []
        with self.torch.no_grad():
            for start in range(0, len(feature_rows), self.batch_size):
                values = self._normalized_tensor(feature_rows[start : start + self.batch_size], means=self.node_means, scales=self.node_scales)
                context = _apply_torch_hidden(values, self.node_hidden_layers, torch=self.torch)
                logits = context.matmul(self.node_output_weight.t()) + self.node_output_bias
                outputs.append(self.torch.softmax(logits, dim=1).cpu())
        return self.torch.cat(outputs, dim=0)

    def _normalized_tensor(self, rows: list[list[float]], *, means: Any, scales: Any) -> Any:
        batch = self.torch.tensor(rows, dtype=self.torch.float32)
        return (batch - means[: batch.shape[1]]) / scales[: batch.shape[1]].clamp_min(1e-6)

    def _uses_context_relation(self) -> bool:
        return (
            self.mode in {"graph_parser_m2", "graph_parser_m3", "graph_parser_m4", "graph_parser_m5"}
            or self.model_version.endswith("_m2")
            or self.model_version.endswith("_m3")
            or self.model_version.endswith("_m4")
            or self.model_version.endswith("_m5")
        )

    def _uses_interaction_relation(self) -> bool:
        return (
            self.mode in {"graph_parser_m3", "graph_parser_m4", "graph_parser_m5"}
            or self.model_version.endswith("_m3")
            or self.model_version.endswith("_m4")
            or self.model_version.endswith("_m5")
        )

    def _uses_keep_relation(self) -> bool:
        return self.mode in {"graph_parser_m4", "graph_parser_m5"} or self.model_version.endswith("_m4") or self.model_version.endswith("_m5")

    def _uses_graph_context_relation(self) -> bool:
        return self.mode == "graph_parser_m5" or self.model_version.endswith("_m5")


def _torch_weights(
    *,
    hidden_weights: tuple[tuple[tuple[float, ...], ...], ...],
    hidden_biases: tuple[tuple[float, ...], ...],
    output_weights: tuple[tuple[float, ...], ...],
    output_bias: tuple[float, ...],
    torch: Any,
) -> tuple[tuple[Any, Any], ...]:
    if not output_weights:
        return ()
    layers: list[tuple[Any, Any]] = []
    for weight, bias in zip(hidden_weights, hidden_biases):
        layers.append((torch.tensor(weight, dtype=torch.float32), torch.tensor(bias, dtype=torch.float32)))
    layers.append((torch.tensor(output_weights, dtype=torch.float32), torch.tensor(output_bias, dtype=torch.float32)))
    return tuple(layers)


def _torch_hidden_layers(
    *,
    hidden_weights: tuple[tuple[tuple[float, ...], ...], ...],
    hidden_biases: tuple[tuple[float, ...], ...],
    torch: Any,
) -> tuple[tuple[Any, Any], ...]:
    return tuple(
        (torch.tensor(weight, dtype=torch.float32), torch.tensor(bias, dtype=torch.float32))
        for weight, bias in zip(hidden_weights, hidden_biases)
    )


def _apply_torch_hidden(values: Any, layers: tuple[tuple[Any, Any], ...], *, torch: Any) -> Any:
    activations = values
    for weight, bias in layers:
        activations = torch.relu(activations.matmul(weight.t()) + bias)
    return activations


def _select_torch_relation_prediction(
    probabilities: Any,
    labels: tuple[str, ...],
    *,
    keep_probability: float | None,
) -> tuple[str, float, float]:
    if keep_probability is None:
        best_index = int(probabilities[: len(labels)].argmax().item())
        confidence = float(probabilities[best_index].item())
        return str(labels[best_index]), confidence, confidence
    positive = []
    for index, label in enumerate(labels):
        if str(label) != "NONE":
            positive.append((index, float(probabilities[index].item())))
    positive_mass = sum(value for _index, value in positive)
    if positive_mass <= 1e-12:
        return "NONE", 0.0, 0.0
    best_index, best_raw_probability = max(positive, key=lambda item: item[1])
    type_confidence = best_raw_probability / positive_mass
    combined_confidence = float(keep_probability) * float(type_confidence)
    return str(labels[best_index]), float(type_confidence), float(combined_confidence)


def _top_torch_relation_alternatives(
    probabilities: Any,
    labels: tuple[str, ...],
    *,
    keep_probability: float | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    scored: list[tuple[int, float]] = []
    if keep_probability is None:
        scored = [
            (index, float(probabilities[index].item()))
            for index in range(min(len(labels), int(probabilities.numel())))
        ]
    else:
        positive = [
            (index, float(probabilities[index].item()))
            for index, label in enumerate(labels)
            if str(label) != "NONE" and index < int(probabilities.numel())
        ]
        positive_mass = sum(value for _index, value in positive)
        if positive_mass > 1e-12:
            scored = [
                (index, float(keep_probability) * (value / positive_mass))
                for index, value in positive
            ]
    alternatives: list[dict[str, Any]] = []
    for index, value in sorted(scored, key=lambda item: item[1], reverse=True):
        label = str(labels[int(index)])
        if label == "NONE":
            continue
        alternatives.append({"relation": label, "confidence": round(float(value), 6)})
        if len(alternatives) >= limit:
            break
    return alternatives


def _top_torch_relation_alternatives_from_indices(
    values: list[float],
    indices: list[int],
    labels: tuple[str, ...],
) -> list[dict[str, Any]]:
    alternatives: list[dict[str, Any]] = []
    for value, index in zip(values, indices):
        if int(index) < 0 or int(index) >= len(labels):
            continue
        label = str(labels[int(index)])
        if label == "NONE" or float(value) < 0.0:
            continue
        alternatives.append({"relation": label, "confidence": round(float(value), 6)})
    return alternatives


def _torch_relation_alternative_sort_key(item: dict[str, Any]) -> tuple[float, str, str]:
    alternatives = item.get("alternatives", []) if isinstance(item, dict) else []
    top_confidence = 0.0
    if alternatives and isinstance(alternatives[0], dict):
        try:
            top_confidence = float(alternatives[0].get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            top_confidence = 0.0
    return (-top_confidence, str(item.get("source", "")), str(item.get("target", "")))


def _decode_rows(
    candidates: list[dict[str, Any]],
    rows_by_id: dict[str, dict[str, Any]],
    *,
    full_verifier: bool = True,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    decoded_rows: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    for candidate in candidates:
        row_id = str(candidate.get("row_id", ""))
        graph_row = rows_by_id.get(row_id, {})
        decoded_row = _decoded_eval_row(row_id, graph_row, candidate, full_verifier=full_verifier)
        warnings.update(str(item) for item in decoded_row.get("warnings", []) if item)
        decoded_rows.append(decoded_row)
    return decoded_rows, warnings


def _decoded_eval_row(
    row_id: str,
    graph_row: dict[str, Any],
    candidate: dict[str, Any],
    *,
    full_verifier: bool = True,
) -> dict[str, Any]:
    from src.core.tinybdmath_latex_decoder import decode_latex_candidate

    decoded = decode_latex_candidate(
        list(graph_row.get("glyph_nodes", []) or graph_row.get("glyphs", []) or []),
        candidate,
        vectors=list(graph_row.get("vector_nodes", []) or graph_row.get("vectors", []) or []),
        fallback_text=_fallback_text(graph_row),
        verify_layout=full_verifier,
    ).to_json()
    label = str(graph_row.get("label_latex", "") or "")
    similarity = _similarity(label, str(decoded.get("latex", "") or ""))
    latex_candidates = _latex_candidate_eval_rows(label, decoded)
    n_best_similarity = max(
        [similarity] + [float(item.get("similarity", 0.0) or 0.0) for item in latex_candidates]
    )
    preferred = _preferred_candidate_eval_row(label, decoded.get("preferred_candidate", {}))
    recommendation = _manual_recommendation_eval_row(label, decoded.get("manual_review_recommendation", {}))
    return {
        "row_id": row_id,
        "kind": str(graph_row.get("kind", "") or candidate.get("kind", "")),
        "label_latex": label,
        "decoded_latex": str(decoded.get("latex", "") or ""),
        "similarity": round(similarity, 6),
        "n_best_similarity": round(n_best_similarity, 6),
        "preferred_candidate_similarity": float(preferred.get("similarity", 0.0) or 0.0),
        "manual_recommendation_similarity": float(recommendation.get("similarity", 0.0) or 0.0),
        "decoded_confidence": float(decoded.get("confidence", 0.0) or 0.0),
        "candidate_abstain": bool(candidate.get("abstain")),
        "decoded_abstain": bool(decoded.get("abstain")),
        "final_abstain": bool(decoded.get("abstain")),
        "layout_status": str(decoded.get("layout_status", "") or "unknown"),
        "layout_confidence": float(decoded.get("layout_confidence", 0.0) or 0.0),
        "layout_warnings": list(decoded.get("layout_warnings", []) or []),
        "latex_candidates": latex_candidates,
        "preferred_candidate": preferred,
        "manual_review_recommendation": recommendation,
        "warnings": list(decoded.get("warnings", []) or []),
    }


def _latex_candidate_eval_rows(label: str, decoded: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in decoded.get("latex_candidates", []) or []:
        if not isinstance(item, dict):
            continue
        latex = str(item.get("latex", "") or "")
        if not latex.strip() or latex in seen:
            continue
        seen.add(latex)
        output.append(
            {
                "rank": int(item.get("rank", len(output) + 1) or len(output) + 1),
                "latex": latex,
                "similarity": round(_similarity(label, latex), 6),
                "confidence": float(item.get("confidence", 0.0) or 0.0),
                "source": str(item.get("source", "") or ""),
                "candidate_only": bool(item.get("candidate_only", True)),
                "accepted": bool(item.get("accepted", False)),
            }
        )
    return sorted(output, key=lambda item: int(item["rank"]))


def _manual_recommendation_eval_row(label: str, recommendation: object) -> dict[str, Any]:
    if not isinstance(recommendation, dict):
        recommendation = {}
    latex = str(recommendation.get("latex", "") or "")
    return {
        "latex": latex,
        "similarity": round(_similarity(label, latex), 6) if latex.strip() else 0.0,
        "recommended_rank": int(recommendation.get("recommended_rank", 0) or 0),
        "confidence": float(recommendation.get("confidence", 0.0) or 0.0),
        "layout_status": str(recommendation.get("layout_status", "") or ""),
        "layout_confidence": float(recommendation.get("layout_confidence", 0.0) or 0.0),
        "selection_blockers": list(recommendation.get("selection_blockers", []) or []),
        "candidate_only": bool(recommendation.get("candidate_only", True)),
        "accepted": bool(recommendation.get("accepted", False)),
        "auto_accept_allowed": bool(recommendation.get("auto_accept_allowed", False)),
    }


def _preferred_candidate_eval_row(label: str, preferred: object) -> dict[str, Any]:
    if not isinstance(preferred, dict):
        preferred = {}
    latex = str(preferred.get("latex", "") or "")
    return {
        "latex": latex,
        "similarity": round(_similarity(label, latex), 6) if latex.strip() else 0.0,
        "recommended_rank": int(preferred.get("recommended_rank", 0) or 0),
        "confidence": float(preferred.get("confidence", 0.0) or 0.0),
        "layout_status": str(preferred.get("layout_status", "") or ""),
        "layout_confidence": float(preferred.get("layout_confidence", 0.0) or 0.0),
        "verifier_score": float(preferred.get("verifier_score", 0.0) or 0.0),
        "selection_blockers": list(preferred.get("selection_blockers", []) or []),
        "requires_cloud_semantic_review": bool(preferred.get("requires_cloud_semantic_review", True)),
        "candidate_only": bool(preferred.get("candidate_only", True)),
        "accepted": bool(preferred.get("accepted", False)),
        "auto_accept_allowed": bool(preferred.get("auto_accept_allowed", False)),
    }


def _decode_rows_stream(
    graph_rows_path: Path,
    candidates_path: Path,
    *,
    limit: int = 0,
    full_verifier: bool = True,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    decoded_rows: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    row_limit = int(limit or 0)
    with graph_rows_path.open("r", encoding="utf-8") as graph_handle, candidates_path.open("r", encoding="utf-8") as candidate_handle:
        while row_limit <= 0 or len(decoded_rows) < row_limit:
            graph_row = _read_next_json_object(graph_handle)
            candidate = _read_next_json_object(candidate_handle)
            if graph_row is None or candidate is None:
                break
            row_id = str(candidate.get("row_id", ""))
            if str(graph_row.get("row_id", "")) != row_id:
                graph_row = {}
                warnings.update(["stream_row_id_mismatch"])
            decoded_row = _decoded_eval_row(row_id, graph_row, candidate, full_verifier=full_verifier)
            warnings.update(str(item) for item in decoded_row.get("warnings", []) if item)
            decoded_rows.append(decoded_row)
    return decoded_rows, warnings


def _build_report(decoded_rows: list[dict[str, Any]], warnings: Counter[str], *, streaming: bool) -> dict[str, Any]:
    metrics = _metrics(decoded_rows)
    return {
        "schema_version": "tinybdmath_decoded_latex_eval_v1",
        "rows": len(decoded_rows),
        "metrics": metrics,
        "n_best_oracle_metrics": _n_best_oracle_metrics(decoded_rows),
        "preferred_candidate_metrics": _preferred_candidate_metrics(decoded_rows),
        "manual_recommendation_metrics": _manual_recommendation_metrics(decoded_rows),
        "layout_gate": _layout_gate_metrics(decoded_rows),
        "warning_counts": dict(sorted(warnings.items())),
        "streaming": bool(streaming),
        "sample_low_similarity": [
            {
                "row_id": row["row_id"],
                "label_latex": row["label_latex"],
                "decoded_latex": row["decoded_latex"],
                "similarity": row["similarity"],
                "n_best_similarity": row.get("n_best_similarity", row["similarity"]),
                "preferred_candidate_similarity": row.get("preferred_candidate_similarity", row["similarity"]),
                "manual_recommendation_similarity": row.get("manual_recommendation_similarity", row["similarity"]),
                "layout_status": row.get("layout_status", "unknown"),
                "final_abstain": bool(row.get("final_abstain")),
                "latex_candidates": row.get("latex_candidates", [])[:3],
                "preferred_candidate": row.get("preferred_candidate", {}),
                "manual_review_recommendation": row.get("manual_review_recommendation", {}),
                "warnings": row["warnings"][:8],
            }
            for row in sorted(decoded_rows, key=lambda item: float(item["similarity"]))[:10]
        ],
        "candidate_only": True,
        "accepted_latex_emitted": False,
        "notes": [
            "This evaluates the final TinyBDMath decoded candidate, not only relation F1.",
            "Labels come from instrumented training/audit graph rows and are not available in production.",
        ],
    }


def _read_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _read_next_json_object(handle: Any) -> dict[str, Any] | None:
    for line in handle:
        text = line.strip()
        if not text:
            continue
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    return None


def _fallback_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for glyph in row.get("glyph_nodes", []) or row.get("glyphs", []) or []:
        if not isinstance(glyph, dict):
            continue
        parts.append(str(glyph.get("unicode", "") or glyph.get("text", "") or ""))
    return "".join(parts)


def _similarity(expected: str, actual: str) -> float:
    from tools.formula_latex_audit import _formula_similarity, _normalize_formula_for_match

    expected_compact = _compact_latex(expected)
    actual_compact = _compact_latex(actual)
    if expected_compact and actual_compact and expected_compact == actual_compact:
        return 1.0
    left = _normalize_formula_for_match(expected)
    right = _normalize_formula_for_match(actual)
    if not left or not right:
        return 0.0
    return _formula_similarity(left, right)


def _compact_latex(value: str) -> str:
    return "".join(str(value or "").split())


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [float(row.get("similarity", 0.0) or 0.0) for row in rows]
    if not scored:
        return {
            "exact_match_rate": 0.0,
            "near_match_rate": 0.0,
            "weak_match_rate": 0.0,
            "average_similarity": 0.0,
            "decoded_nonempty_rate": 0.0,
        }
    return {
        "exact_match_rate": round(sum(1 for value in scored if value >= 0.98) / len(scored), 6),
        "near_match_rate": round(sum(1 for value in scored if value >= 0.80) / len(scored), 6),
        "weak_match_rate": round(sum(1 for value in scored if value >= 0.55) / len(scored), 6),
        "average_similarity": round(sum(scored) / len(scored), 6),
        "decoded_nonempty_rate": round(sum(1 for row in rows if str(row.get("decoded_latex", "") or "").strip()) / len(rows), 6),
    }


def _n_best_oracle_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [float(row.get("n_best_similarity", row.get("similarity", 0.0)) or 0.0) for row in rows]
    candidate_counts = [len(row.get("latex_candidates", []) or []) for row in rows]
    if not scored:
        return {
            "oracle_exact_match_rate": 0.0,
            "oracle_near_match_rate": 0.0,
            "oracle_average_similarity": 0.0,
            "average_candidate_count": 0.0,
        }
    return {
        "oracle_exact_match_rate": round(sum(1 for value in scored if value >= 0.98) / len(scored), 6),
        "oracle_near_match_rate": round(sum(1 for value in scored if value >= 0.80) / len(scored), 6),
        "oracle_average_similarity": round(sum(scored) / len(scored), 6),
        "average_candidate_count": round(sum(candidate_counts) / len(candidate_counts), 6),
    }


def _manual_recommendation_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [float(row.get("manual_recommendation_similarity", row.get("similarity", 0.0)) or 0.0) for row in rows]
    recommendations = [
        row.get("manual_review_recommendation", {})
        for row in rows
        if isinstance(row.get("manual_review_recommendation", {}), dict)
    ]
    if not scored:
        return {
            "recommendation_exact_match_rate": 0.0,
            "recommendation_near_match_rate": 0.0,
            "recommendation_average_similarity": 0.0,
            "non_rank_one_recommendation_rate": 0.0,
            "auto_accept_allowed_count": 0,
        }
    non_rank_one = sum(1 for item in recommendations if int(item.get("recommended_rank", 1) or 1) not in {0, 1})
    auto_accept_allowed = sum(1 for item in recommendations if bool(item.get("auto_accept_allowed", False)))
    return {
        "recommendation_exact_match_rate": round(sum(1 for value in scored if value >= 0.98) / len(scored), 6),
        "recommendation_near_match_rate": round(sum(1 for value in scored if value >= 0.80) / len(scored), 6),
        "recommendation_average_similarity": round(sum(scored) / len(scored), 6),
        "non_rank_one_recommendation_rate": round(non_rank_one / len(scored), 6),
        "auto_accept_allowed_count": int(auto_accept_allowed),
    }


def _preferred_candidate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [float(row.get("preferred_candidate_similarity", row.get("similarity", 0.0)) or 0.0) for row in rows]
    preferred = [
        row.get("preferred_candidate", {})
        for row in rows
        if isinstance(row.get("preferred_candidate", {}), dict)
    ]
    if not scored:
        return {
            "preferred_exact_match_rate": 0.0,
            "preferred_near_match_rate": 0.0,
            "preferred_average_similarity": 0.0,
            "non_rank_one_preferred_rate": 0.0,
            "cloud_review_required_rate": 0.0,
            "auto_accept_allowed_count": 0,
        }
    non_rank_one = sum(1 for item in preferred if int(item.get("recommended_rank", 1) or 1) not in {0, 1})
    cloud_review = sum(1 for item in preferred if bool(item.get("requires_cloud_semantic_review", True)))
    auto_accept_allowed = sum(1 for item in preferred if bool(item.get("auto_accept_allowed", False)))
    return {
        "preferred_exact_match_rate": round(sum(1 for value in scored if value >= 0.98) / len(scored), 6),
        "preferred_near_match_rate": round(sum(1 for value in scored if value >= 0.80) / len(scored), 6),
        "preferred_average_similarity": round(sum(scored) / len(scored), 6),
        "non_rank_one_preferred_rate": round(non_rank_one / len(scored), 6),
        "cloud_review_required_rate": round(cloud_review / len(scored), 6),
        "auto_accept_allowed_count": int(auto_accept_allowed),
    }


def _layout_gate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "status_counts": {},
            "final_abstain_rate": 0.0,
            "pass_rate": 0.0,
            "review_rate": 0.0,
            "pass_or_review_exact_match_rate": 0.0,
            "pass_or_review_near_match_rate": 0.0,
        }
    statuses = Counter(str(row.get("layout_status", "") or "unknown") for row in rows)
    usable = [row for row in rows if not bool(row.get("final_abstain"))]
    usable_metrics = _metrics(usable)
    return {
        "status_counts": dict(sorted(statuses.items())),
        "final_abstain_rate": round(sum(1 for row in rows if bool(row.get("final_abstain"))) / len(rows), 6),
        "pass_rate": round(statuses.get("pass", 0) / len(rows), 6),
        "review_rate": round(statuses.get("review", 0) / len(rows), 6),
        "pass_or_review_exact_match_rate": usable_metrics["exact_match_rate"],
        "pass_or_review_near_match_rate": usable_metrics["near_match_rate"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
