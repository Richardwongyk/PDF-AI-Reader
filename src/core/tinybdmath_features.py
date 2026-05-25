"""Feature generation for TinyBDMath relation models.

This module converts Enriched Glyph Graph nodes into pairwise relation
candidates and numeric features.  It does not decode formulas or emit LaTeX;
the output is training/inference input for later MLP/GNN relation scorers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Literal

from src.core.symbol_identity_repair import EnrichedGlyphGraph, EnrichedGlyphNode


TINYBDMATH_FEATURE_SCHEMA_VERSION = "tinybdmath_edge_candidates_v1"

RelationHint = Literal[
    "right_neighbor",
    "superscript_zone",
    "subscript_zone",
    "above_zone",
    "below_zone",
    "overlap_zone",
    "far_context",
]


@dataclass(frozen=True)
class TinyBDGlyphFeature:
    """Node-level feature vector source for TinyBDMath."""

    node_id: str
    unicode: str
    latex: str
    identity_source: str
    identity_confidence: float
    is_unknown: bool
    font: str
    size: float
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    width: float
    height: float
    page_num: int


@dataclass(frozen=True)
class TinyBDEdgeCandidate:
    """One possible relation edge between two glyphs."""

    edge_id: str
    source: str
    target: str
    hint: RelationHint
    features: dict[str, float | int | str]


@dataclass(frozen=True)
class TinyBDFeatureGraph:
    """Feature graph passed to future TinyBDMath scorers."""

    schema_version: str
    enriched_input_hash: str
    glyphs: tuple[TinyBDGlyphFeature, ...]
    edges: tuple[TinyBDEdgeCandidate, ...]
    warnings: tuple[str, ...]

    @property
    def input_hash(self) -> str:
        payload = self.to_json(include_input_hash=False)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="ignore")
        return hashlib.sha256(encoded).hexdigest()

    def to_json(self, *, include_input_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if include_input_hash:
            payload["input_hash"] = self.input_hash
        return payload


@dataclass(frozen=True)
class TinyBDFeatureConfig:
    """Conservative candidate-generation bounds."""

    max_horizontal_gap_ratio: float = 2.8
    max_vertical_gap_ratio: float = 2.4
    max_candidates_per_node: int = 12
    min_overlap_ratio: float = 0.08


class TinyBDFeatureExtractor:
    """Create relation-candidate features from enriched PDF glyph facts."""

    def __init__(self, config: TinyBDFeatureConfig | None = None) -> None:
        self.config = config or TinyBDFeatureConfig()

    def extract(self, graph: EnrichedGlyphGraph) -> TinyBDFeatureGraph:
        glyphs = tuple(_glyph_feature(node) for node in graph.glyphs if _is_real_glyph(node))
        return self._from_glyph_features(graph.input_hash, glyphs)

    def extract_region(
        self,
        graph: EnrichedGlyphGraph,
        bbox: tuple[float, float, float, float],
        *,
        margin: float = 1.0,
    ) -> TinyBDFeatureGraph:
        expanded = (
            float(bbox[0]) - margin,
            float(bbox[1]) - margin,
            float(bbox[2]) + margin,
            float(bbox[3]) + margin,
        )
        glyphs = tuple(
            _glyph_feature(node)
            for node in graph.glyphs
            if _is_real_glyph(node) and _bbox_intersects(node.raw.bbox, expanded)
        )
        region_hash = _stable_hash({
            "graph": graph.input_hash,
            "bbox": tuple(round(float(value), 6) for value in bbox),
            "margin": round(float(margin), 6),
        })
        return self._from_glyph_features(region_hash, glyphs)

    def _from_glyph_features(
        self,
        enriched_input_hash: str,
        glyphs: tuple[TinyBDGlyphFeature, ...],
    ) -> TinyBDFeatureGraph:
        edges: list[TinyBDEdgeCandidate] = []
        warnings: set[str] = set()
        if not glyphs:
            warnings.add("empty_feature_graph")
        glyph_by_id = {glyph.node_id: glyph for glyph in glyphs}
        for source in glyphs:
            candidates: list[TinyBDEdgeCandidate] = []
            for target in glyphs:
                if source.node_id == target.node_id:
                    continue
                hint = _relation_hint(source, target, self.config)
                if hint is None:
                    continue
                candidates.append(_edge_candidate(source, target, hint))
            candidates = sorted(candidates, key=_edge_sort_key)[: self.config.max_candidates_per_node]
            edges.extend(candidates)
        edges = _dedupe_edges(edges)
        for edge in edges:
            if edge.source not in glyph_by_id or edge.target not in glyph_by_id:
                warnings.add("dangling_edge")
        return TinyBDFeatureGraph(
            schema_version=TINYBDMATH_FEATURE_SCHEMA_VERSION,
            enriched_input_hash=enriched_input_hash,
            glyphs=glyphs,
            edges=tuple(edges),
            warnings=tuple(sorted(warnings)),
        )


def _is_real_glyph(node: EnrichedGlyphNode) -> bool:
    text = node.raw.text or ""
    return bool(text.strip()) or node.raw.is_unknown


def _glyph_feature(node: EnrichedGlyphNode) -> TinyBDGlyphFeature:
    identity = node.resolved_identity
    unicode_value = identity.unicode if identity is not None else ""
    latex = identity.latex if identity is not None else ""
    source = identity.source if identity is not None else "unknown"
    confidence = identity.confidence if identity is not None else 0.0
    bbox = tuple(float(value) for value in node.raw.bbox)
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    return TinyBDGlyphFeature(
        node_id=node.node_id,
        unicode=unicode_value,
        latex=latex,
        identity_source=source,
        identity_confidence=round(float(confidence), 6),
        is_unknown=node.raw.is_unknown and identity is None,
        font=node.raw.normalized_font or node.raw.font,
        size=round(float(node.raw.size), 6),
        bbox=bbox,
        center=(round((bbox[0] + bbox[2]) / 2.0, 6), round((bbox[1] + bbox[3]) / 2.0, 6)),
        width=round(width, 6),
        height=round(height, 6),
        page_num=node.raw.page_num,
    )


def _relation_hint(
    source: TinyBDGlyphFeature,
    target: TinyBDGlyphFeature,
    config: TinyBDFeatureConfig,
) -> RelationHint | None:
    height = max(source.height, target.height, 1.0)
    horizontal_gap = target.bbox[0] - source.bbox[2]
    vertical_delta = target.center[1] - source.center[1]
    abs_vertical = abs(vertical_delta)
    x_overlap = _overlap_ratio((source.bbox[0], source.bbox[2]), (target.bbox[0], target.bbox[2]))
    y_overlap = _overlap_ratio((source.bbox[1], source.bbox[3]), (target.bbox[1], target.bbox[3]))

    if horizontal_gap >= -height * 0.25 and horizontal_gap <= height * config.max_horizontal_gap_ratio:
        if y_overlap >= max(config.min_overlap_ratio, 0.35):
            return "right_neighbor"
        if target.height <= source.height * 0.92 and vertical_delta < -height * 0.12:
            return "superscript_zone"
        if target.height <= source.height * 0.92 and vertical_delta > height * 0.12:
            return "subscript_zone"
    if x_overlap >= config.min_overlap_ratio and abs_vertical <= height * config.max_vertical_gap_ratio:
        if vertical_delta < -height * 0.35:
            return "above_zone"
        if vertical_delta > height * 0.35:
            return "below_zone"
        return "overlap_zone"
    if (
        abs(horizontal_gap) <= height * config.max_horizontal_gap_ratio
        and abs_vertical <= height * config.max_vertical_gap_ratio
    ):
        return "far_context"
    return None


def _edge_candidate(
    source: TinyBDGlyphFeature,
    target: TinyBDGlyphFeature,
    hint: RelationHint,
) -> TinyBDEdgeCandidate:
    height = max(source.height, target.height, 1.0)
    width = max(source.width, target.width, 1.0)
    dx = target.center[0] - source.center[0]
    dy = target.center[1] - source.center[1]
    horizontal_gap = target.bbox[0] - source.bbox[2]
    vertical_gap = max(target.bbox[1] - source.bbox[3], source.bbox[1] - target.bbox[3], 0.0)
    features: dict[str, float | int | str] = {
        "dx_over_height": round(dx / height, 6),
        "dy_over_height": round(dy / height, 6),
        "horizontal_gap_over_height": round(horizontal_gap / height, 6),
        "vertical_gap_over_height": round(vertical_gap / height, 6),
        "x_overlap": round(_overlap_ratio((source.bbox[0], source.bbox[2]), (target.bbox[0], target.bbox[2])), 6),
        "y_overlap": round(_overlap_ratio((source.bbox[1], source.bbox[3]), (target.bbox[1], target.bbox[3])), 6),
        "size_ratio": round(target.size / max(source.size, 1e-6), 6),
        "width_ratio": round(target.width / width, 6),
        "height_ratio": round(target.height / height, 6),
        "same_font": int(source.font == target.font),
        "source_unknown": int(source.is_unknown),
        "target_unknown": int(target.is_unknown),
        "hint": hint,
    }
    return TinyBDEdgeCandidate(
        edge_id=f"{source.node_id}->{target.node_id}:{hint}",
        source=source.node_id,
        target=target.node_id,
        hint=hint,
        features=features,
    )


def _overlap_ratio(
    first: tuple[float, float],
    second: tuple[float, float],
) -> float:
    left = max(first[0], second[0])
    right = min(first[1], second[1])
    overlap = max(0.0, right - left)
    denom = max(min(first[1] - first[0], second[1] - second[0]), 1e-6)
    return overlap / denom


def _edge_sort_key(edge: TinyBDEdgeCandidate) -> tuple[int, float, str]:
    priority = {
        "right_neighbor": 0,
        "superscript_zone": 1,
        "subscript_zone": 1,
        "above_zone": 2,
        "below_zone": 2,
        "overlap_zone": 3,
        "far_context": 4,
    }[edge.hint]
    dx = abs(float(edge.features.get("dx_over_height", 0.0) or 0.0))
    dy = abs(float(edge.features.get("dy_over_height", 0.0) or 0.0))
    return (priority, dx + dy, edge.edge_id)


def _dedupe_edges(edges: list[TinyBDEdgeCandidate]) -> tuple[TinyBDEdgeCandidate, ...]:
    result: dict[str, TinyBDEdgeCandidate] = {}
    for edge in edges:
        result.setdefault(edge.edge_id, edge)
    return tuple(result[key] for key in sorted(result))


def _bbox_intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return left[0] <= right[2] and left[2] >= right[0] and left[1] <= right[3] and left[3] >= right[1]


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="ignore")
    return hashlib.sha256(encoded).hexdigest()
