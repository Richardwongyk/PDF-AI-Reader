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
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
    candidate_pairs,
    graph_parser_predictions_to_structural_candidate,
    graph_nodes,
    graph_parser_features,
    graph_parser_node_features,
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
        help="Use PyTorch batched Graph Parser inference when --graph-parser-model is provided.",
    )
    parser.add_argument("--torch-batch-size", type=int, default=65536)
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
        )
    else:
        graph_rows = _read_jsonl(args.graph_rows, limit=args.limit)
        if args.graph_parser_model is not None:
            if args.torch_inference:
                candidates = _candidates_from_graph_parser_torch(
                    graph_rows,
                    args.graph_parser_model,
                    batch_size=int(args.torch_batch_size),
                )
            else:
                candidates = _candidates_from_graph_parser(graph_rows, args.graph_parser_model)
            if args.candidates_output is not None:
                _write_jsonl(args.candidates_output, candidates)
                print(
                    json.dumps(
                        {
                            "schema_version": "tinybdmath_graph_parser_candidates_export_v1",
                            "rows": len(candidates),
                            "torch_inference": bool(args.torch_inference),
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
        decoded_rows, warnings = _decode_rows(candidates, rows_by_id)
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
) -> list[dict[str, Any]]:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - depends on optional training env
        raise SystemExit(f"torch_unavailable: {exc}") from exc
    predictor = _TorchGraphParserPredictor(
        TinyBDGraphParserArtifact.load(graph_parser_model),
        torch=torch,
        batch_size=max(1, int(batch_size)),
    )
    candidates: list[dict[str, Any]] = []
    for graph_row in graph_rows:
        predictions = predictor.predict_row(graph_row)
        structural = graph_parser_predictions_to_structural_candidate(predictions)
        structural["row_id"] = str(graph_row.get("row_id", "") or "")
        structural["kind"] = str(graph_row.get("kind", "") or "")
        candidates.append(structural)
    return candidates


class _TorchGraphParserPredictor:
    def __init__(self, artifact: TinyBDGraphParserArtifact, *, torch: Any, batch_size: int) -> None:
        self.artifact = artifact
        self.torch = torch
        self.batch_size = max(1, int(batch_size))
        self.relation_weights = _torch_weights(
            hidden_weights=artifact.hidden_weights,
            hidden_biases=artifact.hidden_biases,
            output_weights=artifact.output_weights,
            output_bias=artifact.output_bias,
            torch=torch,
        )
        self.node_weights = _torch_weights(
            hidden_weights=artifact.node_hidden_weights,
            hidden_biases=artifact.node_hidden_biases,
            output_weights=artifact.node_output_weights,
            output_bias=artifact.node_output_bias,
            torch=torch,
        )
        self.relation_means = torch.tensor(artifact.means or [0.0 for _ in artifact.feature_names], dtype=torch.float32)
        self.relation_scales = torch.tensor(artifact.scales or [1.0 for _ in artifact.feature_names], dtype=torch.float32).clamp_min(1e-6)
        self.node_means = torch.tensor(artifact.node_means or [0.0 for _ in artifact.node_feature_names], dtype=torch.float32)
        self.node_scales = torch.tensor(artifact.node_scales or [1.0 for _ in artifact.node_feature_names], dtype=torch.float32).clamp_min(1e-6)

    def predict_row(self, graph_row: dict[str, Any]) -> dict[str, Any]:
        all_nodes = graph_nodes(graph_row, include_blank=True)
        nodes = [node for node in all_nodes if node.get("node_type") == "vector" or str(node.get("text", "") or "").strip()]
        pairs = candidate_pairs(nodes)
        predictions: list[dict[str, Any]] = []
        relation_alternatives: list[dict[str, Any]] = []
        if pairs:
            feature_rows = [
                [float(graph_parser_features(source, target, nodes).get(name, 0.0) or 0.0) for name in self.artifact.feature_names]
                for source, target in pairs
            ]
            probabilities = self._predict_probabilities(
                feature_rows,
                means=self.relation_means,
                scales=self.relation_scales,
                weights=self.relation_weights,
            )
            labels = self.artifact.relation_labels
            cutoff = float(self.artifact.threshold)
            for index, (source, target) in enumerate(pairs):
                row_probabilities = probabilities[index]
                best_index = int(row_probabilities.argmax().item())
                relation = labels[best_index]
                confidence = float(row_probabilities[best_index].item())
                if relation == "NONE" or confidence < cutoff:
                    continue
                relation_alternatives.append(
                    {
                        "source": str(source["node_id"]),
                        "target": str(target["node_id"]),
                        "alternatives": _top_torch_relation_alternatives(row_probabilities, labels),
                    }
                )
                predictions.append(
                    {
                        "source": str(source["node_id"]),
                        "target": str(target["node_id"]),
                        "relation": relation,
                        "confidence": round(confidence, 6),
                    }
                )
        node_predictions = self._predict_node_labels(all_nodes)
        return {
            "schema_version": "tinybdmath_graph_parser_predictions_v1",
            "model_version": self.artifact.model_version,
            "feature_version": self.artifact.feature_version,
            "input_hash": "",
            "node_count": len(nodes),
            "candidate_pairs": len(pairs),
            "node_predictions": node_predictions,
            "node_filter_threshold": float(self.artifact.node_filter_threshold),
            "predictions": sorted(predictions, key=lambda item: (item["source"], item["target"], item["relation"])),
            "relation_alternatives": sorted(
                relation_alternatives,
                key=lambda item: (str(item.get("source", "")), str(item.get("target", ""))),
            ),
            "candidate_only": True,
        }

    def _predict_node_labels(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.node_weights:
            return []
        feature_rows = [
            [float(graph_parser_node_features(node, nodes).get(name, 0.0) or 0.0) for name in self.artifact.node_feature_names]
            for node in nodes
        ]
        if not feature_rows:
            return []
        probabilities = self._predict_probabilities(
            feature_rows,
            means=self.node_means,
            scales=self.node_scales,
            weights=self.node_weights,
        )
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

    def _predict_probabilities(
        self,
        feature_rows: list[list[float]],
        *,
        means: Any,
        scales: Any,
        weights: tuple[tuple[Any, Any], ...],
    ) -> Any:
        if not feature_rows or not weights:
            return self.torch.empty((0, 0), dtype=self.torch.float32)
        outputs: list[Any] = []
        with self.torch.no_grad():
            for start in range(0, len(feature_rows), self.batch_size):
                batch = self.torch.tensor(feature_rows[start : start + self.batch_size], dtype=self.torch.float32)
                activations = (batch - means[: batch.shape[1]]) / scales[: batch.shape[1]].clamp_min(1e-6)
                for layer_index, (weight, bias) in enumerate(weights):
                    activations = activations.matmul(weight.t()) + bias
                    if layer_index < len(weights) - 1:
                        activations = self.torch.relu(activations)
                outputs.append(self.torch.softmax(activations, dim=1).cpu())
        return self.torch.cat(outputs, dim=0)


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


def _top_torch_relation_alternatives(
    probabilities: Any,
    labels: tuple[str, ...],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    values, indices = probabilities[: len(labels)].sort(descending=True)
    alternatives: list[dict[str, Any]] = []
    for value, index in zip(values.tolist(), indices.tolist()):
        label = str(labels[int(index)])
        if label == "NONE":
            continue
        alternatives.append({"relation": label, "confidence": round(float(value), 6)})
        if len(alternatives) >= limit:
            break
    return alternatives


def _decode_rows(
    candidates: list[dict[str, Any]],
    rows_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    decoded_rows: list[dict[str, Any]] = []
    warnings: Counter[str] = Counter()
    for candidate in candidates:
        row_id = str(candidate.get("row_id", ""))
        graph_row = rows_by_id.get(row_id, {})
        decoded_row = _decoded_eval_row(row_id, graph_row, candidate)
        warnings.update(str(item) for item in decoded_row.get("warnings", []) if item)
        decoded_rows.append(decoded_row)
    return decoded_rows, warnings


def _decoded_eval_row(
    row_id: str,
    graph_row: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    from src.core.tinybdmath_latex_decoder import decode_latex_candidate

    decoded = decode_latex_candidate(
        list(graph_row.get("glyph_nodes", []) or graph_row.get("glyphs", []) or []),
        candidate,
        vectors=list(graph_row.get("vector_nodes", []) or graph_row.get("vectors", []) or []),
        fallback_text=_fallback_text(graph_row),
    ).to_json()
    label = str(graph_row.get("label_latex", "") or "")
    similarity = _similarity(label, str(decoded.get("latex", "") or ""))
    latex_candidates = _latex_candidate_eval_rows(label, decoded)
    n_best_similarity = max(
        [similarity] + [float(item.get("similarity", 0.0) or 0.0) for item in latex_candidates]
    )
    recommendation = _manual_recommendation_eval_row(label, decoded.get("manual_review_recommendation", {}))
    return {
        "row_id": row_id,
        "kind": str(graph_row.get("kind", "") or candidate.get("kind", "")),
        "label_latex": label,
        "decoded_latex": str(decoded.get("latex", "") or ""),
        "similarity": round(similarity, 6),
        "n_best_similarity": round(n_best_similarity, 6),
        "manual_recommendation_similarity": float(recommendation.get("similarity", 0.0) or 0.0),
        "decoded_confidence": float(decoded.get("confidence", 0.0) or 0.0),
        "candidate_abstain": bool(candidate.get("abstain")),
        "decoded_abstain": bool(decoded.get("abstain")),
        "final_abstain": bool(decoded.get("abstain")),
        "layout_status": str(decoded.get("layout_status", "") or "unknown"),
        "layout_confidence": float(decoded.get("layout_confidence", 0.0) or 0.0),
        "layout_warnings": list(decoded.get("layout_warnings", []) or []),
        "latex_candidates": latex_candidates,
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


def _decode_rows_stream(
    graph_rows_path: Path,
    candidates_path: Path,
    *,
    limit: int = 0,
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
            decoded_row = _decoded_eval_row(row_id, graph_row, candidate)
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
                "manual_recommendation_similarity": row.get("manual_recommendation_similarity", row["similarity"]),
                "layout_status": row.get("layout_status", "unknown"),
                "final_abstain": bool(row.get("final_abstain")),
                "latex_candidates": row.get("latex_candidates", [])[:3],
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
