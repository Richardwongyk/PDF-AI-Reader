"""Stable schemas for TinyBDMath model experiments.

The first production step is data and evaluation, not a neural network.  These
schemas freeze the row/config/result contracts so later MLP/GNN implementations
can be swapped in without changing dataset generation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


TinyBDModelKind = Literal["mlp_edge_scorer", "gnn_edge_scorer", "graph_transformer"]


@dataclass(frozen=True)
class TinyBDModelConfig:
    model_kind: TinyBDModelKind
    model_version: str
    feature_schema_version: str
    label_version: str = "source_alignment_quality_v1"
    max_edges_per_graph: int = 512
    accepted_precision_target: float = 0.99999
    candidate_recall_target: float = 0.999

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TinyBDModelEvalGate:
    """Quality thresholds before any model can write accepted candidates."""

    min_strong_alignment_rate: float = 0.80
    min_near_alignment_rate: float = 0.92
    max_low_alignment_rate: float = 0.02
    max_unknown_glyph_rate: float = 0.001
    max_p95_latency_ms: int = 25

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TinyBDModelPrediction:
    source: str
    target: str
    relation: str
    confidence: float
    model_kind: TinyBDModelKind
    model_version: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_MLP_EDGE_CONFIG = TinyBDModelConfig(
    model_kind="mlp_edge_scorer",
    model_version="tinybdmath_mlp_edge_v0",
    feature_schema_version="tinybdmath_edge_candidates_v1",
)

DEFAULT_EVAL_GATE = TinyBDModelEvalGate()
