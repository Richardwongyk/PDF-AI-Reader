"""Constrained structural validation for TinyBDMath candidates.

This layer is intentionally before LaTeX serialization.  It validates the
model-produced structure graph against the TinyBDMath relation schema and
reports blockers.  It does not repair strings, infer missing formula templates,
or emit accepted LaTeX.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.tinybdmath_graph_parser import GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD


TINYBDMATH_CONSTRAINED_DECODE_VERSION = "tinybdmath_constrained_decode_v1"

CSLT_RELATION_SCHEMA = {
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

SERIALIZED_RELATION_SUBSET = {
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


@dataclass(frozen=True)
class TinyBDConstrainedDecodeResult:
    version: str
    status: str
    confidence: float
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    semantic_node_count: int
    selected_relation_count: int
    schema_relation_count: int
    serialized_relation_count: int
    relation_node_coverage: float
    unsupported_relations: tuple[str, ...]
    nonserialized_relations: tuple[str, ...]
    canonical_cslt: dict[str, Any] = field(default_factory=dict)
    n_best_cslt: tuple[dict[str, Any], ...] = ()
    candidate_only: bool = True
    accepted: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def constrain_structural_candidate(
    glyphs: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    *,
    vectors: list[dict[str, Any]] | None = None,
) -> TinyBDConstrainedDecodeResult:
    filtered_node_ids = _model_filtered_node_ids(structural_candidate)
    semantic_nodes = _semantic_nodes(glyphs, filtered_node_ids)
    semantic_node_ids = {_node_id(item) for item in semantic_nodes if _node_id(item)}
    vector_node_ids = {_node_id(item) for item in vectors or [] if _node_id(item)}
    node_ids = semantic_node_ids | vector_node_ids
    relations = _dedupe_relations(structural_candidate.get("selected_relations", []) or [])
    schema_relations = [
        item
        for item in relations
        if str(item.get("relation", "") or "") in CSLT_RELATION_SCHEMA
    ]
    serialized_relations = [
        item
        for item in schema_relations
        if str(item.get("relation", "") or "") in SERIALIZED_RELATION_SUBSET
    ]
    unsupported_relations = sorted(
        {
            str(item.get("relation", "") or "")
            for item in relations
            if str(item.get("relation", "") or "") not in CSLT_RELATION_SCHEMA
        }
    )
    nonserialized_relations = sorted(
        {
            str(item.get("relation", "") or "")
            for item in schema_relations
            if str(item.get("relation", "") or "") not in SERIALIZED_RELATION_SUBSET
        }
    )
    relation_node_ids = _semantic_relation_node_ids(schema_relations, semantic_node_ids)
    coverage = round(len(relation_node_ids) / len(semantic_node_ids), 6) if semantic_node_ids else 0.0
    blockers = _blockers(
        structural_candidate=structural_candidate,
        semantic_node_count=len(semantic_nodes),
        node_ids=node_ids,
        relations=relations,
        unsupported_relations=unsupported_relations,
    )
    warnings = _warnings(
        semantic_node_count=len(semantic_nodes),
        relations=relations,
        schema_relations=schema_relations,
        serialized_relations=serialized_relations,
        nonserialized_relations=nonserialized_relations,
        coverage=coverage,
        filtered_node_count=len(filtered_node_ids),
    )
    confidence = _confidence(
        semantic_node_count=len(semantic_nodes),
        schema_relations=schema_relations,
        coverage=coverage,
        blockers=blockers,
        warnings=warnings,
    )
    status = "abstain" if blockers else ("review" if warnings else "pass")
    canonical_cslt = _canonical_cslt(
        semantic_nodes=semantic_nodes,
        vectors=vectors or [],
        relations=schema_relations,
        coverage=coverage,
        status=status,
        confidence=confidence,
    )
    n_best_cslt = _n_best_cslt(
        canonical_cslt,
        selected_relations=schema_relations,
        relation_alternatives=list(structural_candidate.get("relation_alternatives", []) or []),
        semantic_nodes=semantic_nodes,
        vectors=vectors or [],
    )
    return TinyBDConstrainedDecodeResult(
        version=TINYBDMATH_CONSTRAINED_DECODE_VERSION,
        status=status,
        confidence=confidence,
        blockers=tuple(sorted(blockers)),
        warnings=tuple(sorted(warnings)),
        semantic_node_count=len(semantic_nodes),
        selected_relation_count=len(relations),
        schema_relation_count=len(schema_relations),
        serialized_relation_count=len(serialized_relations),
        relation_node_coverage=coverage,
        unsupported_relations=tuple(unsupported_relations),
        nonserialized_relations=tuple(nonserialized_relations),
        canonical_cslt=canonical_cslt,
        n_best_cslt=tuple(n_best_cslt),
    )


def _blockers(
    *,
    structural_candidate: dict[str, Any],
    semantic_node_count: int,
    node_ids: set[str],
    relations: list[dict[str, Any]],
    unsupported_relations: list[str],
) -> set[str]:
    blockers: set[str] = set()
    if "no_graph_parser_model" in str(structural_candidate.get("model_version", "") or ""):
        blockers.add("constraint_graph_parser_model_missing")
    if semantic_node_count <= 0:
        blockers.add("constraint_no_semantic_nodes")
    if unsupported_relations:
        blockers.add("constraint_unsupported_relation_schema")
    for relation in relations:
        source = str(relation.get("source", "") or "")
        target = str(relation.get("target", "") or "")
        if source and source not in node_ids:
            blockers.add("constraint_relation_source_missing")
        if target and target not in node_ids:
            blockers.add("constraint_relation_target_missing")
    if _has_directed_cycle(relations):
        blockers.add("constraint_relation_cycle")
    return blockers


def _warnings(
    *,
    semantic_node_count: int,
    relations: list[dict[str, Any]],
    schema_relations: list[dict[str, Any]],
    serialized_relations: list[dict[str, Any]],
    nonserialized_relations: list[str],
    coverage: float,
    filtered_node_count: int,
) -> set[str]:
    warnings: set[str] = set()
    if filtered_node_count > 0:
        warnings.add("constraint_filtered_spacing_nodes")
    if semantic_node_count > 1 and not relations:
        warnings.add("constraint_no_selected_relations")
    if relations and not schema_relations:
        warnings.add("constraint_no_schema_relations")
    if schema_relations and not serialized_relations:
        warnings.add("constraint_schema_relations_not_serialized")
    if nonserialized_relations:
        warnings.add("constraint_nonserialized_relation_labels")
    if semantic_node_count > 2 and schema_relations and coverage < 0.50:
        warnings.add("constraint_low_relation_node_coverage")
    return warnings


def _confidence(
    *,
    semantic_node_count: int,
    schema_relations: list[dict[str, Any]],
    coverage: float,
    blockers: set[str],
    warnings: set[str],
) -> float:
    if schema_relations:
        base = sum(_float(item.get("confidence")) for item in schema_relations) / len(schema_relations)
        base = max(base, min(1.0, coverage))
    elif semantic_node_count == 1:
        base = 0.70
    else:
        base = 0.0
    penalty = min(0.90, 0.35 * len(blockers) + 0.05 * len(warnings))
    return round(max(0.0, min(1.0, base - penalty)), 6)


def _dedupe_relations(items: list[Any]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("source", "") or ""),
            str(item.get("target", "") or ""),
            str(item.get("relation", "") or ""),
        )
        current = best.get(key)
        if current is None or _float(item.get("confidence")) > _float(current.get("confidence")):
            best[key] = dict(item)
    return sorted(best.values(), key=lambda item: (str(item.get("source", "")), str(item.get("target", "")), str(item.get("relation", ""))))


def _semantic_relation_node_ids(
    relations: list[dict[str, Any]],
    semantic_node_ids: set[str],
) -> set[str]:
    output: set[str] = set()
    for relation in relations:
        for key in ("source", "target"):
            node_id = str(relation.get(key, "") or "")
            if node_id in semantic_node_ids:
                output.add(node_id)
    return output


def _canonical_cslt(
    *,
    semantic_nodes: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    coverage: float,
    status: str,
    confidence: float,
    candidate_id: str = "selected",
    rank: int = 1,
) -> dict[str, Any]:
    nodes = [_canonical_node(item, node_type="glyph") for item in semantic_nodes]
    nodes.extend(_canonical_node(item, node_type="vector") for item in vectors if _node_id(item))
    canonical_relations = [_canonical_relation(item, index=index) for index, item in enumerate(relations)]
    target_ids = {
        str(item.get("target", "") or "")
        for item in canonical_relations
        if str(item.get("source", "") or "") != str(item.get("target", "") or "")
    }
    node_ids = [str(item.get("node_id", "") or "") for item in nodes if item.get("node_id")]
    roots = [node_id for node_id in node_ids if node_id not in target_ids]
    return {
        "schema_version": "tinybdmath_cslt_candidate_v1",
        "candidate_id": candidate_id,
        "rank": int(rank),
        "status": status,
        "confidence": round(float(confidence or 0.0), 6),
        "nodes": sorted(nodes, key=lambda item: (item["sort_key"], item["node_id"])),
        "relations": canonical_relations,
        "root_node_ids": roots,
        "semantic_node_count": len(semantic_nodes),
        "relation_count": len(canonical_relations),
        "relation_node_coverage": round(float(coverage or 0.0), 6),
        "candidate_only": True,
        "accepted": False,
    }


def _n_best_cslt(
    canonical_cslt: dict[str, Any],
    *,
    selected_relations: list[dict[str, Any]],
    relation_alternatives: list[Any],
    semantic_nodes: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    candidates: dict[tuple[tuple[str, str, str], ...], dict[str, Any]] = {
        _relation_signature(selected_relations): dict(canonical_cslt)
    }

    def add_candidate(relations: list[dict[str, Any]], *, candidate_id: str, rank: int) -> None:
        schema_relations = [
            item
            for item in _dedupe_relations(relations)
            if str(item.get("relation", "") or "") in CSLT_RELATION_SCHEMA
        ]
        signature = _relation_signature(schema_relations)
        if signature in candidates:
            return
        relation_node_ids = _semantic_relation_node_ids(
            schema_relations,
            {_node_id(item) for item in semantic_nodes if _node_id(item)},
        )
        coverage = round(len(relation_node_ids) / len(semantic_nodes), 6) if semantic_nodes else 0.0
        confidence = _average_relation_confidence(schema_relations)
        candidates[signature] = _canonical_cslt(
            semantic_nodes=semantic_nodes,
            vectors=vectors,
            relations=schema_relations,
            coverage=coverage,
            status="candidate",
            confidence=confidence,
            candidate_id=candidate_id,
            rank=rank,
        )

    if _has_directed_cycle(selected_relations):
        projected = _acyclic_relation_projection(selected_relations)
        if projected:
            add_candidate(projected, candidate_id="acyclic_projection", rank=2)

    selected_by_pair = {
        (str(item.get("source", "") or ""), str(item.get("target", "") or "")): item
        for item in selected_relations
    }
    rank = 2 + (1 if _has_directed_cycle(selected_relations) else 0)
    for item in relation_alternatives:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", "") or "")
        target = str(item.get("target", "") or "")
        if not source or not target:
            continue
        current = selected_by_pair.get((source, target))
        for alternative in item.get("alternatives", []) or []:
            if not isinstance(alternative, dict):
                continue
            relation = str(alternative.get("relation", "") or "")
            if relation not in CSLT_RELATION_SCHEMA:
                continue
            if current is not None and relation == str(current.get("relation", "") or ""):
                continue
            replaced = [
                relation_item
                for relation_item in selected_relations
                if (str(relation_item.get("source", "") or ""), str(relation_item.get("target", "") or ""))
                != (source, target)
            ]
            replaced.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "confidence": float(alternative.get("confidence", 0.0) or 0.0),
                    "hint": "graph_parser_alternative",
                    "reason": "graph_parser_relation_alternative",
                }
            )
            add_candidate(replaced, candidate_id=f"alternative_{rank - 1}", rank=rank)
            rank += 1
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    selected_signature = _relation_signature(selected_relations)
    selected = dict(candidates.get(selected_signature, canonical_cslt))
    alternatives = [
        item
        for signature, item in candidates.items()
        if signature != selected_signature
    ]
    output = [selected] + sorted(
        alternatives,
        key=lambda item: (-float(item.get("confidence", 0.0) or 0.0), int(item.get("rank", 999))),
    )[: max(0, limit - 1)]
    for index, item in enumerate(output, start=1):
        item["rank"] = index
    return output or [canonical_cslt]


def _acyclic_relation_projection(relations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for relation in sorted(
        relations,
        key=lambda item: (
            -_float(item.get("confidence")),
            str(item.get("source", "") or ""),
            str(item.get("target", "") or ""),
            str(item.get("relation", "") or ""),
        ),
    ):
        candidate = projected + [dict(relation, reason="acyclic_confidence_projection")]
        if not _has_directed_cycle(candidate):
            projected = candidate
    return sorted(
        projected,
        key=lambda item: (
            str(item.get("source", "") or ""),
            str(item.get("target", "") or ""),
            str(item.get("relation", "") or ""),
        ),
    )


def _canonical_node(item: dict[str, Any], *, node_type: str) -> dict[str, Any]:
    node_id = _node_id(item)
    bbox = item.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raw = item.get("raw", {}) if isinstance(item.get("raw", {}), dict) else {}
        bbox = raw.get("bbox", [0.0, 0.0, 0.0, 0.0])
    bbox_values = [_float(value) for value in list(bbox)[:4]]
    text = str(item.get("latex", "") or item.get("unicode", "") or item.get("text", "") or "")
    raw = item.get("raw", {}) if isinstance(item.get("raw", {}), dict) else {}
    if not text:
        text = str(raw.get("text", "") or "")
    return {
        "node_id": node_id,
        "node_type": str(item.get("node_type", "") or node_type),
        "text": text,
        "bbox": bbox_values,
        "sort_key": [_float(bbox_values[0]), _float(bbox_values[1]), node_id],
    }


def _canonical_relation(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    relation = str(item.get("relation", "") or "")
    return {
        "edge_id": str(item.get("edge_id", "") or f"cslt{index:05d}"),
        "source": str(item.get("source", "") or ""),
        "target": str(item.get("target", "") or ""),
        "relation": relation,
        "confidence": round(_float(item.get("confidence")), 6),
        "serialized": relation in SERIALIZED_RELATION_SUBSET,
        "hint": str(item.get("hint", "") or ""),
        "reason": str(item.get("reason", "") or ""),
    }


def _relation_signature(relations: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                str(item.get("source", "") or ""),
                str(item.get("target", "") or ""),
                str(item.get("relation", "") or ""),
            )
            for item in relations
        )
    )


def _average_relation_confidence(relations: list[dict[str, Any]]) -> float:
    if not relations:
        return 0.0
    return round(sum(_float(item.get("confidence")) for item in relations) / len(relations), 6)


def _has_directed_cycle(relations: list[dict[str, Any]]) -> bool:
    graph: dict[str, set[str]] = {}
    for relation in relations:
        source = str(relation.get("source", "") or "")
        target = str(relation.get("target", "") or "")
        kind = str(relation.get("relation", "") or "")
        if not source or not target or source == target:
            continue
        if kind in {"FENCE_BODY"}:
            continue
        graph.setdefault(source, set()).add(target)
    visiting: set[str] = set()
    visited: set[str] = set()
    for node_id in graph:
        if _cycle_from(node_id, graph, visiting, visited):
            return True
    return False


def _cycle_from(
    node_id: str,
    graph: dict[str, set[str]],
    visiting: set[str],
    visited: set[str],
) -> bool:
    if node_id in visited:
        return False
    if node_id in visiting:
        return True
    visiting.add(node_id)
    for target in graph.get(node_id, set()):
        if _cycle_from(target, graph, visiting, visited):
            return True
    visiting.remove(node_id)
    visited.add(node_id)
    return False


def _semantic_nodes(
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
