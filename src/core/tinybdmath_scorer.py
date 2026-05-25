"""Candidate scoring layer for TinyBDMath quality models.

The scorer is deliberately conservative: it turns PDF-derived feature rows into
quality predictions and audit payloads, but it does not rewrite LaTeX or mark a
formula accepted.  Accepted write-back remains a separate verifier/fusion gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.core.tinybdmath_baseline import TinyBDBaselineModel, row_features


@dataclass(frozen=True)
class TinyBDCandidateQualityScore:
    candidate_id: str
    model_version: str
    predicted_label: str
    confidence: float
    probabilities: dict[str, float]
    gate: dict[str, Any]
    feature_summary: dict[str, float]
    warnings: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class TinyBDCandidateQualityScorer:
    """Score TinyBDMath candidates with a saved baseline model."""

    def __init__(self, model: TinyBDBaselineModel) -> None:
        self.model = model

    @classmethod
    def from_model_path(cls, path: Path) -> "TinyBDCandidateQualityScorer":
        return cls(TinyBDBaselineModel.load(path))

    def score_row(self, row: dict[str, Any]) -> TinyBDCandidateQualityScore:
        features = row_features(row)
        probabilities = self.model.predict_proba(features)
        predicted = max(probabilities, key=lambda key: probabilities[key]) if probabilities else ""
        gate = self.model.gate_decision(features)
        return TinyBDCandidateQualityScore(
            candidate_id=str(row.get("candidate_id", "")),
            model_version=self.model.model_version,
            predicted_label=predicted,
            confidence=float(probabilities.get(predicted, 0.0)),
            probabilities=probabilities,
            gate=gate,
            feature_summary=_feature_summary(row, features),
            warnings=_score_warnings(row, gate),
        )

    def score_rows(self, rows: list[dict[str, Any]]) -> list[TinyBDCandidateQualityScore]:
        return [self.score_row(row) for row in rows]


def _feature_summary(row: dict[str, Any], features: dict[str, float]) -> dict[str, float]:
    keys = (
        "glyph_count_log",
        "edge_count_log",
        "edge_per_glyph",
        "unknown_glyph_rate",
        "repaired_count_log",
        "feature_density",
        "structural_signal_rate",
        "subscript_rate",
        "superscript_rate",
        "above_rate",
        "below_rate",
        "far_context_rate",
    )
    summary = {key: round(float(features.get(key, 0.0) or 0.0), 6) for key in keys}
    summary["glyph_count"] = float(row.get("glyph_count", 0) or 0)
    summary["edge_count"] = float(row.get("edge_count", 0) or 0)
    return summary


def _score_warnings(row: dict[str, Any], gate: dict[str, Any]) -> tuple[str, ...]:
    warnings: list[str] = []
    if float(row.get("unknown_glyph_rate", 0.0) or 0.0) > 0.0:
        warnings.append("unknown_glyphs_remaining")
    if int(row.get("edge_count", 0) or 0) <= 0:
        warnings.append("empty_or_edge_less_feature_graph")
    if gate.get("accepted_candidate"):
        warnings.append("candidate_gate_positive_requires_external_verifier")
    if str(row.get("latex_target", "") or ""):
        warnings.append("row_contains_test_only_latex_target")
    return tuple(warnings)
