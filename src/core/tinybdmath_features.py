"""Feature generation for TinyBDMath PDF graph models.

This module converts Enriched Glyph Graph nodes into pairwise relation
candidates and numeric features.  It does not decode formulas or emit LaTeX;
the output is training/inference input for the Graph Parser path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import replace
import hashlib
import json
import bisect
from typing import Any, Literal

from src.core.symbol_identity_repair import EnrichedGlyphGraph, EnrichedGlyphNode


TINYBDMATH_FEATURE_SCHEMA_VERSION = "tinybdmath_edge_candidates_v2_vector_rule_radical"

RelationHint = Literal[
    "right_neighbor",
    "superscript_zone",
    "subscript_zone",
    "above_zone",
    "below_zone",
    "overlap_zone",
    "far_context",
    "above_rule_candidate",
    "below_rule_candidate",
    "fraction_bar_candidate",
    "overline_candidate",
    "radical_body_candidate",
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
    is_script_size: bool = False


@dataclass(frozen=True)
class TinyBDVectorFeature:
    """Vector/rule node used for fractions, radicals and overlines."""

    node_id: str
    node_type: str
    vector_type: str
    bbox: tuple[float, float, float, float]
    center: tuple[float, float]
    width: float
    height: float
    aspect_ratio: float
    page_num: int
    is_horizontal_rule_candidate: bool


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
    """Feature graph passed to TinyBDMath graph parsing."""

    schema_version: str
    enriched_input_hash: str
    glyphs: tuple[TinyBDGlyphFeature, ...]
    vectors: tuple[TinyBDVectorFeature, ...]
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
        vectors = tuple(_vector_feature(node) for node in graph.raw_graph.vectors)
        return self._from_features(graph.input_hash, glyphs, vectors)

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
        vectors = tuple(
            _vector_feature(node)
            for node in graph.raw_graph.vectors
            if _bbox_intersects(node.bbox, expanded)
        )
        region_hash = _stable_hash({
            "graph": graph.input_hash,
            "bbox": tuple(round(float(value), 6) for value in bbox),
            "margin": round(float(margin), 6),
        })
        return self._from_features(region_hash, glyphs, vectors)

    def extract_from_enriched_json(
        self,
        *,
        enriched_input_hash: str,
        glyphs_json: list[dict[str, Any]],
        vectors_json: list[dict[str, Any]] | None = None,
    ) -> TinyBDFeatureGraph:
        """Build candidate features from persisted Enriched Glyph Graph JSON."""
        glyphs = tuple(
            feature
            for item in glyphs_json
            if (feature := _glyph_feature_from_json(item)) is not None
        )
        vectors = tuple(
            feature
            for item in vectors_json or []
            if (feature := _vector_feature_from_json(item)) is not None
        )
        return self._from_features(enriched_input_hash, glyphs, vectors)

    def _from_features(
        self,
        enriched_input_hash: str,
        glyphs: tuple[TinyBDGlyphFeature, ...],
        vectors: tuple[TinyBDVectorFeature, ...] = (),
    ) -> TinyBDFeatureGraph:
        glyphs = _mark_script_size(glyphs)
        edges: list[TinyBDEdgeCandidate] = []
        warnings: set[str] = set()
        if not glyphs:
            warnings.add("empty_feature_graph")
        node_by_id = {glyph.node_id: glyph for glyph in glyphs}
        node_by_id.update({vector.node_id: vector for vector in vectors})
        ordered_x = sorted(glyphs, key=lambda item: (item.bbox[0], item.center[1], item.node_id))
        x0_values = [item.bbox[0] for item in ordered_x]
        index_by_id = {item.node_id: index for index, item in enumerate(ordered_x)}
        for source in glyphs:
            candidates: list[TinyBDEdgeCandidate] = []
            for target in _local_glyph_targets(source, ordered_x, x0_values, index_by_id):
                if source.node_id == target.node_id:
                    continue
                hint = _relation_hint(source, target, self.config)
                if hint is None:
                    continue
                candidates.append(_edge_candidate(source, target, hint))
            candidates = sorted(candidates, key=_edge_sort_key)[: self.config.max_candidates_per_node]
            edges.extend(candidates)
            radical_candidates = [
                _edge_candidate(source, target, "radical_body_candidate")
                for target in glyphs
                if source.node_id != target.node_id and _radical_body_hint(source, target)
            ]
            edges.extend(sorted(radical_candidates, key=_edge_sort_key)[: self.config.max_candidates_per_node])
        for vector in vectors:
            if not vector.is_horizontal_rule_candidate:
                continue
            above = [glyph for glyph in glyphs if _glyph_overlaps_rule(glyph, vector) and glyph.center[1] < vector.center[1]]
            below = [glyph for glyph in glyphs if _glyph_overlaps_rule(glyph, vector) and glyph.center[1] > vector.center[1]]
            for glyph in sorted(above, key=lambda item: (item.center[0], item.center[1], item.node_id))[:24]:
                edges.append(_edge_candidate(vector, glyph, "above_rule_candidate"))
            for glyph in sorted(below, key=lambda item: (item.center[0], item.center[1], item.node_id))[:24]:
                edges.append(_edge_candidate(vector, glyph, "below_rule_candidate"))
            if above and below:
                edges.append(_edge_candidate(vector, vector, "fraction_bar_candidate"))
            elif above:
                edges.append(_edge_candidate(vector, vector, "overline_candidate"))
        edges = _dedupe_edges(edges)
        for edge in edges:
            if edge.source not in node_by_id or edge.target not in node_by_id:
                warnings.add("dangling_edge")
        return TinyBDFeatureGraph(
            schema_version=TINYBDMATH_FEATURE_SCHEMA_VERSION,
            enriched_input_hash=enriched_input_hash,
            glyphs=glyphs,
            vectors=vectors,
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
        is_script_size=False,
    )


def _glyph_feature_from_json(item: dict[str, Any]) -> TinyBDGlyphFeature | None:
    raw = item.get("raw", {})
    if not isinstance(raw, dict):
        return None
    bbox_value = raw.get("bbox", ())
    if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        return None
    try:
        bbox = tuple(float(value) for value in bbox_value)
    except (TypeError, ValueError):
        return None
    identity = item.get("resolved_identity", {})
    if not isinstance(identity, dict):
        identity = {}
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    return TinyBDGlyphFeature(
        node_id=str(item.get("node_id", "") or raw.get("node_id", "")),
        unicode=str(identity.get("unicode", "") or ""),
        latex=str(identity.get("latex", "") or ""),
        identity_source=str(identity.get("source", "unknown") or "unknown"),
        identity_confidence=round(_json_float(identity.get("confidence")), 6),
        is_unknown=bool(raw.get("is_unknown")) and not identity,
        font=str(raw.get("normalized_font", "") or raw.get("font", "") or ""),
        size=round(_json_float(raw.get("size")), 6),
        bbox=bbox,  # type: ignore[arg-type]
        center=(round((bbox[0] + bbox[2]) / 2.0, 6), round((bbox[1] + bbox[3]) / 2.0, 6)),
        width=round(width, 6),
        height=round(height, 6),
        page_num=_json_int(raw.get("page_num")),
        is_script_size=bool(item.get("is_script_size")) or bool(raw.get("is_script_size")),
    )


def _vector_feature(node: Any) -> TinyBDVectorFeature:
    bbox = tuple(float(value) for value in node.bbox)
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    aspect = width / max(height, 1e-6)
    return TinyBDVectorFeature(
        node_id=str(getattr(node, "node_id", "") or ""),
        node_type="vector",
        vector_type=str(getattr(node, "kind", "vector") or "vector"),
        bbox=bbox,  # type: ignore[arg-type]
        center=(round((bbox[0] + bbox[2]) / 2.0, 6), round((bbox[1] + bbox[3]) / 2.0, 6)),
        width=round(width, 6),
        height=round(height, 6),
        aspect_ratio=round(aspect, 6),
        page_num=_json_int(getattr(node, "page_num", 0)),
        is_horizontal_rule_candidate=_is_horizontal_rule(width, height),
    )


def _vector_feature_from_json(item: dict[str, Any]) -> TinyBDVectorFeature | None:
    if not isinstance(item, dict):
        return None
    bbox_value = item.get("bbox", ())
    if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        return None
    try:
        bbox = tuple(float(value) for value in bbox_value)
    except (TypeError, ValueError):
        return None
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    aspect = width / max(height, 1e-6)
    return TinyBDVectorFeature(
        node_id=str(item.get("node_id", "") or item.get("id", "") or ""),
        node_type=str(item.get("node_type", "") or item.get("kind", "") or "vector"),
        vector_type=str(item.get("vector_type", "") or item.get("kind", "") or "vector"),
        bbox=bbox,  # type: ignore[arg-type]
        center=(round((bbox[0] + bbox[2]) / 2.0, 6), round((bbox[1] + bbox[3]) / 2.0, 6)),
        width=round(width, 6),
        height=round(height, 6),
        aspect_ratio=round(aspect, 6),
        page_num=_json_int(item.get("page_num")),
        is_horizontal_rule_candidate=bool(item.get("is_horizontal_rule_candidate")) or _is_horizontal_rule(width, height),
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


def _local_glyph_targets(
    source: TinyBDGlyphFeature,
    ordered_x: list[TinyBDGlyphFeature],
    x0_values: list[float],
    index_by_id: dict[str, int],
) -> list[TinyBDGlyphFeature]:
    if len(ordered_x) <= 72:
        return ordered_x
    left = source.bbox[0] - 4.0 * max(source.height, 1.0)
    right = source.bbox[2] + 4.0 * max(source.height, 1.0)
    start = max(0, bisect.bisect_left(x0_values, left) - 8)
    end = min(len(ordered_x), bisect.bisect_right(x0_values, right) + 8)
    pool: dict[str, TinyBDGlyphFeature] = {item.node_id: item for item in ordered_x[start:end]}
    source_index = index_by_id.get(source.node_id, 0)
    for item in ordered_x[max(0, source_index - 36) : min(len(ordered_x), source_index + 48)]:
        pool[item.node_id] = item
    overlap_end = bisect.bisect_right(x0_values, source.bbox[2])
    overlap = [item for item in ordered_x[:overlap_end] if item.bbox[2] >= source.bbox[0]]
    overlap.sort(key=lambda item: (abs(item.center[1] - source.center[1]), item.center[0], item.node_id))
    for item in overlap[:36]:
        pool[item.node_id] = item
    return list(pool.values())


def _edge_candidate(
    source: TinyBDGlyphFeature | TinyBDVectorFeature,
    target: TinyBDGlyphFeature | TinyBDVectorFeature,
    hint: RelationHint,
) -> TinyBDEdgeCandidate:
    height = max(float(source.height), float(target.height), 1.0)
    width = max(float(source.width), float(target.width), 1.0)
    dx = target.center[0] - source.center[0]
    dy = target.center[1] - source.center[1]
    horizontal_gap = target.bbox[0] - source.bbox[2]
    vertical_gap = max(target.bbox[1] - source.bbox[3], source.bbox[1] - target.bbox[3], 0.0)
    source_size = float(getattr(source, "size", source.height) or source.height or 1.0)
    target_size = float(getattr(target, "size", target.height) or target.height or 1.0)
    source_font = str(getattr(source, "font", "") or "")
    target_font = str(getattr(target, "font", "") or "")
    features: dict[str, float | int | str] = {
        "dx_over_height": round(dx / height, 6),
        "dy_over_height": round(dy / height, 6),
        "horizontal_gap_over_height": round(horizontal_gap / height, 6),
        "vertical_gap_over_height": round(vertical_gap / height, 6),
        "x_overlap": round(_overlap_ratio((source.bbox[0], source.bbox[2]), (target.bbox[0], target.bbox[2])), 6),
        "y_overlap": round(_overlap_ratio((source.bbox[1], source.bbox[3]), (target.bbox[1], target.bbox[3])), 6),
        "size_ratio": round(target_size / max(source_size, 1e-6), 6),
        "width_ratio": round(target.width / width, 6),
        "height_ratio": round(target.height / height, 6),
        "same_font": int(bool(source_font) and source_font == target_font),
        "source_unknown": int(bool(getattr(source, "is_unknown", False))),
        "target_unknown": int(bool(getattr(target, "is_unknown", False))),
        "source_is_script_size": int(bool(getattr(source, "is_script_size", False))),
        "target_is_script_size": int(bool(getattr(target, "is_script_size", False))),
        "hint": hint,
    }
    return TinyBDEdgeCandidate(
        edge_id=f"{source.node_id}->{target.node_id}:{hint}",
        source=source.node_id,
        target=target.node_id,
        hint=hint,
        features=features,
    )


def _radical_body_hint(source: TinyBDGlyphFeature, target: TinyBDGlyphFeature) -> bool:
    if _glyph_latex(source) not in {r"\sqrt", "√", "sqrt"}:
        return False
    if target.center[0] <= source.center[0]:
        return False
    height = max(source.height, target.height, 1.0)
    horizontal_gap = target.bbox[0] - source.bbox[2]
    if horizontal_gap > height * 4.0:
        return False
    vertical_overlap = _overlap_ratio((source.bbox[1], source.bbox[3]), (target.bbox[1], target.bbox[3]))
    return vertical_overlap >= 0.05 or abs(target.center[1] - source.center[1]) <= height * 1.6


def _glyph_latex(glyph: TinyBDGlyphFeature) -> str:
    return str(glyph.latex or glyph.unicode or "")


def _mark_script_size(glyphs: tuple[TinyBDGlyphFeature, ...]) -> tuple[TinyBDGlyphFeature, ...]:
    sizes = sorted(float(glyph.size) for glyph in glyphs if float(glyph.size) > 0.0)
    if not sizes:
        return glyphs
    reference = sizes[len(sizes) // 2]
    if reference <= 0.0:
        return glyphs
    return tuple(
        replace(glyph, is_script_size=bool(glyph.is_script_size or float(glyph.size) <= reference * 0.84))
        for glyph in glyphs
    )


def _glyph_overlaps_rule(glyph: TinyBDGlyphFeature, vector: TinyBDVectorFeature) -> bool:
    return _overlap_ratio((glyph.bbox[0], glyph.bbox[2]), (vector.bbox[0], vector.bbox[2])) >= 0.05


def _is_horizontal_rule(width: float, height: float) -> bool:
    return width / max(height, 1e-6) >= 6.0 and height <= max(width * 0.08, 2.0)


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
        "radical_body_candidate": 1,
        "above_rule_candidate": 5,
        "below_rule_candidate": 5,
        "fraction_bar_candidate": 6,
        "overline_candidate": 6,
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


def _json_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _json_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
