"""Candidate-only structural evidence for TinyBDMath relation scores.

This layer consumes scored graph edges and selects a conservative structural
candidate graph.  It deliberately does not emit accepted LaTeX.  The output is
an auditable bridge for later SLT/MathML decoders, verifiers, r2a fusion, and
manual review.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


STRUCTURAL_CANDIDATE_SCHEMA_VERSION = "tinybdmath_structural_candidate_v2_vector_rule_radical"
STRUCTURAL_DECODER_VERSION = "tinybdmath_candidate_structure_decoder_v1_vector_rule_radical"
SLT_SKELETON_VERSION = "tinybdmath_slt_skeleton_v1_vector_rule_radical"
AMBIGUOUS_RULE_RELATIONS = frozenset({"FRACTION_BAR", "OVERLINE", "ABOVE", "BELOW", "HORIZONTAL", "RADICAL_BODY"})
SELECTABLE_RELATIONS = frozenset({"HORIZONTAL", "SUP", "SUB", "ABOVE", "BELOW", "FRACTION_BAR", "OVERLINE", "RADICAL_BODY"})


@dataclass(frozen=True)
class TinyBDSelectedRelation:
    edge_id: str
    source: str
    target: str
    relation: str
    confidence: float
    hint: str
    reason: str


@dataclass(frozen=True)
class TinyBDStructuralCandidate:
    schema_version: str
    decoder_version: str
    row_id: str
    case: str
    kind: str
    page_num: int | None
    graph_input_hash: str
    model_version: str
    selected_relations: tuple[TinyBDSelectedRelation, ...]
    slt_skeleton: dict[str, Any]
    verifier_report: dict[str, Any]
    relation_summary: dict[str, Any]
    ambiguity_report: dict[str, Any]
    verifier_warnings: tuple[str, ...]
    abstain: bool
    candidate_only: bool = True

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def build_structural_candidate(
    scored_graph: dict[str, Any],
    *,
    min_confidence: float = 0.70,
    max_outgoing_per_source: int = 4,
) -> TinyBDStructuralCandidate:
    """Build a conservative candidate structure from scored relation edges."""

    scores = _score_items(scored_graph)
    selected = _select_relations(
        scores,
        min_confidence=float(min_confidence),
        max_outgoing_per_source=max(1, int(max_outgoing_per_source)),
    )
    inherited_warnings = [str(item) for item in scored_graph.get("verifier_warnings", []) if item]
    ambiguity = _ambiguity_report(scores, selected)
    slt_skeleton = _slt_skeleton(scored_graph, selected)
    verifier = _verifier_report(scored_graph, selected, ambiguity, slt_skeleton)
    warnings = _candidate_warnings(scored_graph, selected, ambiguity, verifier, inherited_warnings)
    abstain = _should_abstain(selected, warnings)
    return TinyBDStructuralCandidate(
        schema_version=STRUCTURAL_CANDIDATE_SCHEMA_VERSION,
        decoder_version=STRUCTURAL_DECODER_VERSION,
        row_id=str(scored_graph.get("row_id", "")),
        case=str(scored_graph.get("case", "")),
        kind=str(scored_graph.get("kind", "")),
        page_num=_optional_int(scored_graph.get("page_num")),
        graph_input_hash=str(scored_graph.get("graph_input_hash", "")),
        model_version=str(scored_graph.get("model_version", "")),
        selected_relations=tuple(selected),
        slt_skeleton=slt_skeleton,
        verifier_report=verifier,
        relation_summary=_summary(selected),
        ambiguity_report=ambiguity,
        verifier_warnings=tuple(sorted(set(warnings))),
        abstain=abstain,
    )


def build_structural_candidates(
    scored_rows: list[dict[str, Any]],
    *,
    min_confidence: float = 0.70,
    max_outgoing_per_source: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = [
        build_structural_candidate(
            row,
            min_confidence=min_confidence,
            max_outgoing_per_source=max_outgoing_per_source,
        ).to_json()
        for row in scored_rows
    ]
    warnings = Counter(warning for row in candidates for warning in row.get("verifier_warnings", []))
    relation_counts = Counter(
        relation.get("relation", "")
        for row in candidates
        for relation in row.get("selected_relations", [])
    )
    manifest = {
        "schema_version": "tinybdmath_structural_candidate_manifest_v2_vector_rule_radical",
        "decoder_version": STRUCTURAL_DECODER_VERSION,
        "rows": len(candidates),
        "selected_relations": sum(len(row.get("selected_relations", [])) for row in candidates),
        "abstain_rows": sum(1 for row in candidates if row.get("abstain")),
        "relation_counts": dict(sorted(relation_counts.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "min_confidence": float(min_confidence),
        "max_outgoing_per_source": int(max_outgoing_per_source),
        "candidate_only": True,
        "accepted_latex_emitted": False,
    }
    return candidates, manifest


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


def write_structural_candidates(rows: list[dict[str, Any]], manifest: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "tinybdmath_structural_candidates.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_dir / "tinybdmath_structural_candidate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _select_relations(
    scores: list[dict[str, Any]],
    *,
    min_confidence: float,
    max_outgoing_per_source: int,
) -> list[TinyBDSelectedRelation]:
    selected: list[TinyBDSelectedRelation] = []
    outgoing: defaultdict[str, int] = defaultdict(int)
    outgoing_relation: defaultdict[tuple[str, str], int] = defaultdict(int)
    incoming_relation: defaultdict[tuple[str, str], int] = defaultdict(int)
    best_by_pair_relation: dict[tuple[str, str, str], dict[str, Any]] = {}
    for score in scores:
        relation = str(score.get("predicted_relation", ""))
        confidence = _float(score.get("confidence"))
        if relation not in SELECTABLE_RELATIONS or confidence < min_confidence:
            continue
        key = (str(score.get("source", "")), str(score.get("target", "")), relation)
        current = best_by_pair_relation.get(key)
        if current is None or confidence > _float(current.get("confidence")):
            best_by_pair_relation[key] = score
    ordered = sorted(best_by_pair_relation.values(), key=_selection_sort_key)
    for score in ordered:
        source = str(score.get("source", ""))
        target = str(score.get("target", ""))
        relation = str(score.get("predicted_relation", ""))
        if outgoing[source] >= max_outgoing_per_source:
            continue
        if _relation_outgoing_limit(relation) <= outgoing_relation[(source, relation)]:
            continue
        if _relation_incoming_limit(relation) <= incoming_relation[(target, relation)]:
            continue
        outgoing[source] += 1
        outgoing_relation[(source, relation)] += 1
        incoming_relation[(target, relation)] += 1
        selected.append(
            TinyBDSelectedRelation(
                edge_id=str(score.get("edge_id", "")),
                source=source,
                target=target,
                relation=relation,
                confidence=round(_float(score.get("confidence")), 6),
                hint=str(score.get("hint", "")),
                reason=_selection_reason(score),
            )
        )
    return sorted(selected, key=lambda item: (item.source, item.target, item.relation, item.edge_id))


def _selection_sort_key(score: dict[str, Any]) -> tuple[int, float, float, str]:
    relation = str(score.get("predicted_relation", ""))
    features = score.get("features", {})
    if not isinstance(features, dict):
        features = {}
    distance = abs(_float(features.get("dx_over_height"))) + abs(_float(features.get("dy_over_height")))
    relation_priority = {
        "FRACTION_BAR": 0,
        "OVERLINE": 0,
        "RADICAL_BODY": 0,
        "SUP": 1,
        "SUB": 1,
        "ABOVE": 2,
        "BELOW": 2,
        "HORIZONTAL": 3,
    }.get(relation, 9)
    return (relation_priority, -_float(score.get("confidence")), distance, str(score.get("edge_id", "")))


def _relation_outgoing_limit(relation: str) -> int:
    if relation == "HORIZONTAL":
        return 1
    if relation in {"SUP", "SUB", "ABOVE", "BELOW", "FRACTION_BAR", "OVERLINE", "RADICAL_BODY"}:
        return 1
    return 4


def _relation_incoming_limit(relation: str) -> int:
    if relation in {"HORIZONTAL", "SUP", "SUB", "ABOVE", "BELOW"}:
        return 1
    return 4


def _ambiguity_report(scores: list[dict[str, Any]], selected: list[TinyBDSelectedRelation]) -> dict[str, Any]:
    by_source = defaultdict(Counter)
    rule_sources: set[str] = set()
    for score in scores:
        hint = str(score.get("hint", ""))
        relation = str(score.get("predicted_relation", ""))
        source = str(score.get("source", ""))
        if "rule" in hint or "fraction_bar" in hint or "overline" in hint:
            rule_sources.add(source)
            if relation in AMBIGUOUS_RULE_RELATIONS:
                by_source[source][relation] += 1
    selected_by_source = defaultdict(set)
    for item in selected:
        if item.relation in AMBIGUOUS_RULE_RELATIONS:
            selected_by_source[item.source].add(item.relation)
    ambiguous_sources = []
    unresolved_sources = []
    for source in sorted(rule_sources):
        candidates = dict(sorted(by_source[source].items()))
        picked = sorted(selected_by_source.get(source, set()))
        if len(candidates) > 1:
            ambiguous_sources.append({"source": source, "candidate_relations": candidates, "selected": picked})
        if not picked:
            unresolved_sources.append(source)
    return {
        "rule_sources": len(rule_sources),
        "ambiguous_rule_sources": ambiguous_sources,
        "unresolved_rule_sources": unresolved_sources,
    }


def _candidate_warnings(
    scored_graph: dict[str, Any],
    selected: list[TinyBDSelectedRelation],
    ambiguity: dict[str, Any],
    verifier: dict[str, Any],
    inherited: list[str],
) -> list[str]:
    warnings = list(inherited)
    selected_relations = Counter(item.relation for item in selected)
    if not selected:
        warnings.append("structural_candidate_empty")
    if ambiguity.get("ambiguous_rule_sources"):
        warnings.append("horizontal_rule_ambiguous")
    if ambiguity.get("unresolved_rule_sources"):
        warnings.append("horizontal_rule_unresolved")
    relation_summary = scored_graph.get("relation_summary", {})
    if isinstance(relation_summary, dict) and relation_summary.get("edge_count", 0) and not selected:
        warnings.append("all_scored_edges_below_selection_threshold")
    if selected_relations.get("SUP", 0) and selected_relations.get("SUB", 0):
        warnings.append("has_script_relations_candidate")
    if selected_relations.get("FRACTION_BAR", 0) and (
        selected_relations.get("ABOVE", 0) == 0 or selected_relations.get("BELOW", 0) == 0
    ):
        warnings.append("fraction_bar_missing_above_or_below_relation")
    for warning in verifier.get("warnings", []):
        warnings.append(str(warning))
    return warnings


def _should_abstain(selected: list[TinyBDSelectedRelation], warnings: list[str]) -> bool:
    hard = {
        "structural_candidate_empty",
        "horizontal_rule_ambiguous",
        "horizontal_rule_unresolved",
        "expected_subscript_not_scored",
        "expected_superscript_not_scored",
        "expected_fraction_bar_not_scored",
        "fraction_bar_missing_above_or_below_relation",
    }
    return not selected or any(warning in hard for warning in warnings)


def _summary(selected: list[TinyBDSelectedRelation]) -> dict[str, Any]:
    counts = Counter(item.relation for item in selected)
    return {
        "selected_relation_count": len(selected),
        "relation_counts": dict(sorted(counts.items())),
        "average_confidence": round(sum(item.confidence for item in selected) / len(selected), 6) if selected else 0.0,
    }


def _slt_skeleton(scored_graph: dict[str, Any], selected: list[TinyBDSelectedRelation]) -> dict[str, Any]:
    node_ids = _node_ids(scored_graph, selected)
    children: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    parents: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected:
        edge = {
            "target": item.target,
            "relation": item.relation,
            "confidence": item.confidence,
            "edge_id": item.edge_id,
        }
        children[item.source].append(edge)
        parents[item.target].append(
            {
                "source": item.source,
                "relation": item.relation,
                "confidence": item.confidence,
                "edge_id": item.edge_id,
            }
        )
    roots = sorted(node for node in node_ids if not parents.get(node))
    isolated = sorted(node for node in node_ids if not parents.get(node) and not children.get(node))
    ordered_nodes = []
    for node in sorted(node_ids):
        ordered_nodes.append(
            {
                "node_id": node,
                "children": sorted(children.get(node, []), key=lambda edge: (edge["relation"], edge["target"], edge["edge_id"])),
                "parents": sorted(parents.get(node, []), key=lambda edge: (edge["relation"], edge["source"], edge["edge_id"])),
            }
        )
    return {
        "schema_version": SLT_SKELETON_VERSION,
        "nodes": ordered_nodes,
        "roots": roots,
        "isolated_nodes": isolated,
        "relation_count": len(selected),
        "node_count": len(node_ids),
        "coverage": round((len(node_ids) - len(isolated)) / len(node_ids), 6) if node_ids else 0.0,
        "candidate_only": True,
    }


def _verifier_report(
    scored_graph: dict[str, Any],
    selected: list[TinyBDSelectedRelation],
    ambiguity: dict[str, Any],
    slt_skeleton: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    relation_counts = Counter(item.relation for item in selected)
    multi_parent_nodes = [
        node["node_id"]
        for node in slt_skeleton.get("nodes", [])
        if isinstance(node, dict) and len(node.get("parents", [])) > 1
    ]
    if multi_parent_nodes:
        warnings.append("slt_node_has_multiple_parents")
    if slt_skeleton.get("node_count", 0) and slt_skeleton.get("coverage", 0.0) < 0.5 and len(selected) > 1:
        warnings.append("low_slt_node_coverage")
    if relation_counts.get("FRACTION_BAR", 0) and (
        relation_counts.get("ABOVE", 0) == 0 or relation_counts.get("BELOW", 0) == 0
    ):
        warnings.append("fraction_bar_incomplete_slt")
    if ambiguity.get("ambiguous_rule_sources"):
        warnings.append("ambiguous_rule_blocks_slt")
    if scored_graph.get("relation_summary", {}).get("edge_count", 0) and not selected:
        warnings.append("no_selected_relation_for_scored_graph")
    return {
        "schema_version": "tinybdmath_structural_verifier_v0",
        "candidate_only": True,
        "checks": {
            "node_count": int(slt_skeleton.get("node_count", 0) or 0),
            "relation_count": len(selected),
            "root_count": len(slt_skeleton.get("roots", []) or []),
            "isolated_node_count": len(slt_skeleton.get("isolated_nodes", []) or []),
            "coverage": float(slt_skeleton.get("coverage", 0.0) or 0.0),
            "multi_parent_nodes": multi_parent_nodes,
            "relation_counts": dict(sorted(relation_counts.items())),
        },
        "warnings": sorted(set(warnings)),
        "passed_for_candidate": not warnings,
        "passed_for_accepted": False,
        "accepted_blocker": "TinyBDMath structural skeleton is candidate evidence; accepted LaTeX requires external decoder and gate.",
    }


def _node_ids(scored_graph: dict[str, Any], selected: list[TinyBDSelectedRelation]) -> set[str]:
    node_ids: set[str] = set()
    for item in selected:
        if item.source:
            node_ids.add(item.source)
        if item.target:
            node_ids.add(item.target)
    for score in _score_items(scored_graph):
        source = str(score.get("source", "") or "")
        target = str(score.get("target", "") or "")
        if source:
            node_ids.add(source)
        if target:
            node_ids.add(target)
    return node_ids


def _score_items(scored_graph: dict[str, Any]) -> list[dict[str, Any]]:
    items = scored_graph.get("relation_scores", [])
    return [item for item in items if isinstance(item, dict)]


def _selection_reason(score: dict[str, Any]) -> str:
    hint = str(score.get("hint", ""))
    relation = str(score.get("predicted_relation", ""))
    if "rule" in hint or "fraction_bar" in hint or "overline" in hint:
        return f"model_selected_{relation.lower()}_from_rule_hint"
    if hint:
        return f"model_selected_{relation.lower()}_from_{hint}"
    return f"model_selected_{relation.lower()}"


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
