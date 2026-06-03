"""Evidence-based verifier for TinyBDMath decoded candidates.

This module does not rewrite LaTeX and does not infer missing formula
templates.  It only decides whether a decoded candidate has enough model and
layout evidence to remain usable as a candidate, or whether it should abstain.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.tinybdmath_constrained_decode import constrain_structural_candidate
from src.core.tinybdmath_graph_parser import GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD


TINYBDMATH_LAYOUT_VERIFIER_VERSION = "tinybdmath_layout_verifier_v1"

LAYOUT_SUPPORTED_RELATIONS = {
    "HORIZONTAL",
    "SUP",
    "SUB",
    "PRE_SUP",
    "PRE_SUB",
    "UNDER",
    "OVER",
    "ABOVE",
    "BELOW",
    "RADICAL_BODY",
    "RADICAL_INDEX",
    "FRACTION_BAR",
    "OVERLINE",
    "UNDERLINE",
    "ACCENT_BASE",
    "TEXT_RUN_NEXT",
    "FENCE_BODY",
    "FENCE_OPEN",
    "FENCE_CLOSE",
    "MATRIX_ROW",
    "MATRIX_CELL",
    "CELL_CONTENT",
}

BLOCKING_DECODER_WARNINGS = {
    "decoder_cycle": "layout_decoder_cycle",
    "decoder_empty_output": "layout_decoder_empty_output",
    "decoder_no_glyphs": "layout_decoder_no_glyphs",
    "decoder_relation_references_missing_glyph": "layout_decoder_relation_references_missing_glyph",
    "decoder_fraction_missing_group": "layout_decoder_fraction_missing_group",
    "decoder_radical_missing_body_relation": "layout_decoder_radical_missing_body_relation",
}

REVIEW_DECODER_WARNINGS = {
    "decoder_no_root": "layout_decoder_no_root",
    "decoder_no_supported_relations": "layout_decoder_no_supported_relations",
    "decoder_unsupported_relation_labels": "layout_decoder_unsupported_relation_labels",
    "decoder_overline_missing_group": "layout_decoder_overline_missing_group",
    "decoder_underline_missing_group": "layout_decoder_underline_missing_group",
    "decoder_accent_missing_group": "layout_decoder_accent_missing_group",
    "decoder_ignored_backward_text_run_next": "layout_decoder_ignored_backward_text_run_next",
    "decoder_text_run_cycle": "layout_decoder_text_run_cycle",
    "graph_parser_no_selected_relations": "layout_graph_parser_no_selected_relations",
}


@dataclass(frozen=True)
class TinyBDLayoutVerification:
    verifier_version: str
    status: str
    confidence: float
    warnings: tuple[str, ...]
    semantic_node_count: int
    selected_relation_count: int
    supported_relation_count: int
    relation_node_coverage: float
    constrained_decode: dict[str, Any] = field(default_factory=dict)
    candidate_only: bool = True
    accepted: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def verify_layout_candidate(
    glyphs: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    *,
    decoded_latex: str,
    decoded_confidence: float,
    decoder_warnings: tuple[str, ...] | list[str],
    vectors: list[dict[str, Any]] | None = None,
) -> TinyBDLayoutVerification:
    filtered_node_ids = _model_filtered_node_ids(structural_candidate)
    constrained = constrain_structural_candidate(
        glyphs,
        structural_candidate,
        vectors=vectors,
    )
    semantic_nodes = _semantic_glyph_nodes(glyphs, filtered_node_ids)
    semantic_node_ids = {_node_id(item) for item in semantic_nodes if _node_id(item)}
    vector_node_ids = {_node_id(item) for item in vectors or [] if _node_id(item)}
    selected_relations = [
        item
        for item in structural_candidate.get("selected_relations", []) or []
        if isinstance(item, dict)
    ]
    supported_relations = [
        item
        for item in selected_relations
        if str(item.get("relation", "") or "") in LAYOUT_SUPPORTED_RELATIONS
    ]
    relation_semantic_nodes: set[str] = set()
    for relation in supported_relations:
        for key in ("source", "target"):
            node_id = str(relation.get(key, "") or "")
            if node_id in semantic_node_ids:
                relation_semantic_nodes.add(node_id)

    relation_node_coverage = (
        round(len(relation_semantic_nodes) / len(semantic_node_ids), 6)
        if semantic_node_ids
        else 0.0
    )
    warnings = _verification_warnings(
        semantic_node_count=len(semantic_nodes),
        selected_relations=selected_relations,
        supported_relations=supported_relations,
        relation_node_coverage=relation_node_coverage,
        decoded_latex=decoded_latex,
        decoded_confidence=float(decoded_confidence or 0.0),
        decoder_warnings=tuple(str(item) for item in decoder_warnings if item),
        structural_candidate=structural_candidate,
        vector_node_ids=vector_node_ids,
        constrained_decode=constrained.to_json(),
    )
    confidence = _verification_confidence(
        semantic_node_count=len(semantic_nodes),
        decoded_latex=decoded_latex,
        decoded_confidence=float(decoded_confidence or 0.0),
        relation_node_coverage=relation_node_coverage,
        warnings=warnings,
    )
    status = _verification_status(warnings, confidence)
    return TinyBDLayoutVerification(
        verifier_version=TINYBDMATH_LAYOUT_VERIFIER_VERSION,
        status=status,
        confidence=confidence,
        warnings=tuple(sorted(warnings)),
        semantic_node_count=len(semantic_nodes),
        selected_relation_count=len(selected_relations),
        supported_relation_count=len(supported_relations),
        relation_node_coverage=relation_node_coverage,
        constrained_decode=constrained.to_json(),
    )


def _verification_warnings(
    *,
    semantic_node_count: int,
    selected_relations: list[dict[str, Any]],
    supported_relations: list[dict[str, Any]],
    relation_node_coverage: float,
    decoded_latex: str,
    decoded_confidence: float,
    decoder_warnings: tuple[str, ...],
    structural_candidate: dict[str, Any],
    vector_node_ids: set[str],
    constrained_decode: dict[str, Any],
) -> set[str]:
    warnings: set[str] = set()
    for blocker in constrained_decode.get("blockers", []) or []:
        warnings.add(str(blocker))
    for warning in constrained_decode.get("warnings", []) or []:
        warnings.add(str(warning))
    if str(constrained_decode.get("status", "") or "") == "abstain":
        warnings.add("layout_constrained_decode_abstain")
    if not str(decoded_latex or "").strip():
        warnings.add("layout_empty_decoded_latex")
    if "no_graph_parser_model" in str(structural_candidate.get("model_version", "") or ""):
        warnings.add("layout_graph_parser_model_missing")
    if bool(structural_candidate.get("abstain")) and semantic_node_count > 1:
        warnings.add("layout_structural_candidate_abstained")
    if semantic_node_count <= 0:
        warnings.add("layout_no_semantic_glyphs")
    if semantic_node_count > 1 and not selected_relations:
        warnings.add("layout_no_selected_relations")
    unsupported_count = len(selected_relations) - len(supported_relations)
    if unsupported_count > 0:
        warnings.add("layout_unsupported_relation_labels")
    if semantic_node_count > 1 and selected_relations and not supported_relations:
        warnings.add("layout_no_supported_relations")
    if semantic_node_count > 2 and supported_relations and relation_node_coverage < 0.50:
        warnings.add("layout_low_relation_node_coverage")
    if semantic_node_count > 1 and decoded_confidence < 0.25:
        warnings.add("layout_low_decoded_confidence")
    if vector_node_ids and not any(
        str(item.get("source", "") or "") in vector_node_ids
        or str(item.get("target", "") or "") in vector_node_ids
        for item in supported_relations
    ):
        warnings.add("layout_vector_evidence_unused")
    for warning in decoder_warnings:
        mapped = BLOCKING_DECODER_WARNINGS.get(warning) or REVIEW_DECODER_WARNINGS.get(warning)
        if mapped:
            warnings.add(mapped)
    return warnings


def _verification_confidence(
    *,
    semantic_node_count: int,
    decoded_latex: str,
    decoded_confidence: float,
    relation_node_coverage: float,
    warnings: set[str],
) -> float:
    if semantic_node_count == 1 and str(decoded_latex or "").strip() and decoded_confidence <= 0:
        base = 0.70
    else:
        base = max(0.0, min(1.0, float(decoded_confidence or 0.0)))
    if relation_node_coverage > 0:
        base = max(base, min(base + 0.10, relation_node_coverage))
    blocking_count = sum(1 for warning in warnings if _is_blocking_warning(warning))
    review_count = max(0, len(warnings) - blocking_count)
    penalty = min(0.80, (0.35 * blocking_count) + min(0.30, 0.06 * review_count))
    return round(max(0.0, min(1.0, base - penalty)), 6)


def _verification_status(warnings: set[str], confidence: float) -> str:
    if any(_is_blocking_warning(warning) for warning in warnings):
        return "abstain"
    if confidence < 0.35:
        return "abstain"
    if warnings or confidence < 0.70:
        return "review"
    return "pass"


def _is_blocking_warning(warning: str) -> bool:
    return warning in {
        "layout_empty_decoded_latex",
        "layout_structural_candidate_abstained",
        "layout_graph_parser_model_missing",
        "layout_constrained_decode_abstain",
        "layout_no_semantic_glyphs",
        "layout_no_selected_relations",
        "layout_no_supported_relations",
        "layout_low_decoded_confidence",
        *BLOCKING_DECODER_WARNINGS.values(),
    }


def _semantic_glyph_nodes(
    glyphs: list[dict[str, Any]],
    filtered_node_ids: set[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for glyph in glyphs:
        if not isinstance(glyph, dict):
            continue
        node_id = _node_id(glyph)
        if node_id and node_id in filtered_node_ids:
            continue
        text = str(glyph.get("latex", "") or glyph.get("unicode", "") or "")
        raw = glyph.get("raw", {}) if isinstance(glyph.get("raw", {}), dict) else {}
        text = text or str(raw.get("text", "") or "")
        if text.strip():
            output.append(glyph)
    return output


def _model_filtered_node_ids(structural_candidate: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    threshold = _float(
        structural_candidate.get(
            "node_filter_threshold",
            GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD,
        )
    )
    if threshold <= 0:
        threshold = GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD
    for item in structural_candidate.get("node_predictions", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("label", "") or "") != "SPACING":
            continue
        if _float(item.get("confidence")) < threshold:
            continue
        node_id = str(item.get("node_id", "") or "")
        if node_id:
            output.add(node_id)
    return output


def _node_id(item: dict[str, Any]) -> str:
    return str(item.get("node_id", "") or item.get("id", "") or "")


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
