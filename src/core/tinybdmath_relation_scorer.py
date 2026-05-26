"""Score TinyBDMath graph rows with an edge relation model.

This module turns a saved edge baseline into candidate relation evidence.  It
does not decode final LaTeX and never marks formulas accepted; it prepares the
auditable relation layer that r2a/fusion/verifier can consume.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from src.core.tinybdmath_edge_baseline import TinyBDEdgeBaselineModel


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
        return asdict(self)


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
        for edge in row.get("candidate_edges", [])[: self.max_edges]:
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


def _summary(scores: list[TinyBDRelationScore]) -> dict[str, Any]:
    counts = Counter(score.predicted_relation for score in scores)
    strong = sum(1 for score in scores if score.confidence >= 0.80 and score.predicted_relation != "LOW_CONFIDENCE")
    return {
        "edge_count": len(scores),
        "relation_counts": dict(sorted(counts.items())),
        "strong_relation_edges": strong,
        "average_confidence": round(sum(score.confidence for score in scores) / len(scores), 6) if scores else 0.0,
    }


def _verifier_warnings(row: dict[str, Any], scores: list[TinyBDRelationScore]) -> list[str]:
    warnings: list[str] = []
    tags = set(str(tag) for tag in row.get("coverage_tags", []))
    predicted = Counter(score.predicted_relation for score in scores)
    if not scores:
        warnings.append("no_relation_scores")
    if predicted.get("LOW_CONFIDENCE", 0) > max(3, len(scores) // 3):
        warnings.append("many_low_confidence_edges")
    if "subscript" in tags and predicted.get("SUB", 0) == 0:
        warnings.append("expected_subscript_not_scored")
    if "superscript" in tags and predicted.get("SUP", 0) == 0:
        warnings.append("expected_superscript_not_scored")
    if "fraction" in tags and predicted.get("FRACTION_BAR", 0) == 0:
        warnings.append("expected_fraction_bar_not_scored")
    if "single_glyph_or_empty_text" in tags and len(scores) > 8:
        warnings.append("single_glyph_has_many_edges")
    return sorted(set(warnings))


def _best(probabilities: dict[str, float]) -> tuple[str, float]:
    if not probabilities:
        return "LOW_CONFIDENCE", 0.0
    label, confidence = max(probabilities.items(), key=lambda item: item[1])
    return label, float(confidence)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
