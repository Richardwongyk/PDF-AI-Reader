"""Audit-grade PDF graph to CSLT alignment for TinyBDMath training labels."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any

from src.core.tinybdmath_symbol_equivalence import TinyBDSymbolEquivalence
from src.core.tinybdmath_cslt_schema import CSLTEdge, CSLTNode, CSLTTree, cslt_from_json


ALIGNMENT_SCHEMA_VERSION = "tinybdmath_alignment_v1"
ALIGNMENT_BUILDER_VERSION = "tinybdmath_pdf_graph_to_cslt_alignment_m1"

SEMANTIC_NODE_TYPES = {"symbol", "text_run"}
STRUCTURE_RELATION_TO_LABEL = {
    "base": "BASE",
    "sub": "SUB",
    "sup": "SUP",
    "numerator": "NUMERATOR",
    "denominator": "DENOMINATOR",
    "radical_body": "RADICAL_BODY",
    "radical_index": "RADICAL_INDEX",
    "accent_base": "ACCENT_BASE",
    "fence_body": "FENCE_BODY",
    "fence_open": "FENCE_OPEN",
    "fence_close": "FENCE_CLOSE",
    "matrix_row": "MATRIX_ROW",
    "matrix_cell": "MATRIX_CELL",
    "cell_content": "CELL_CONTENT",
    "child": "CHILD",
    "next": "NEXT",
}
_SYMBOL_EQUIVALENCE = TinyBDSymbolEquivalence()


@dataclass(frozen=True)
class TinyBDNodeAlignment:
    pdf_node_id: str
    target_node_id: str
    confidence: float
    label: str
    reason: str
    target_key: str = ""
    target_node_type: str = ""
    target_value: str = ""
    target_latex: str = ""
    target_attrs: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TinyBDRelationLabel:
    source: str
    target: str
    relation: str
    confidence: float
    supervision: str
    target_edge: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TinyBDAlignmentResult:
    row_id: str
    input_hash: str
    alignment_version: str
    node_alignments: tuple[TinyBDNodeAlignment, ...]
    relation_labels: tuple[TinyBDRelationLabel, ...]
    structure_labels: tuple[dict[str, Any], ...]
    ignored_pdf_nodes: tuple[dict[str, Any], ...]
    unmatched_target_nodes: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    stats: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": ALIGNMENT_SCHEMA_VERSION,
            "row_id": self.row_id,
            "input_hash": self.input_hash,
            "alignment_version": self.alignment_version,
            "node_alignments": [item.to_json() for item in self.node_alignments],
            "relation_labels": [item.to_json() for item in self.relation_labels],
            "structure_labels": list(self.structure_labels),
            "ignored_pdf_nodes": list(self.ignored_pdf_nodes),
            "unmatched_target_nodes": list(self.unmatched_target_nodes),
            "warnings": list(self.warnings),
            "stats": self.stats,
        }


class TinyBDAlignmentBuilder:
    """Build hard/soft/ignore supervision from graph rows and target CSLT."""

    def align_row(self, graph_row: dict[str, Any], target_row: dict[str, Any]) -> TinyBDAlignmentResult:
        row_id = str(graph_row.get("row_id", "") or target_row.get("row_id", "") or "")
        target_payload = target_row.get("target_tree")
        if not isinstance(target_payload, dict):
            return _empty_result(row_id, graph_row, target_row, "missing_target_tree")
        tree = cslt_from_json(target_payload)
        pdf_nodes = _pdf_nodes(graph_row)
        target_leaves = _target_leaf_units(tree)
        node_alignments, used_pdf_ids, used_target_ids, warnings = _align_leaf_units(pdf_nodes, target_leaves)
        ignored_pdf_nodes = tuple(
            {
                "pdf_node_id": node["node_id"],
                "reason": _ignore_reason(node),
                "text": node.get("text", ""),
            }
            for node in pdf_nodes
            if node["node_id"] not in used_pdf_ids
        )
        unmatched_target_nodes = tuple(
            {
                "target_node_id": leaf.node.node_id,
                "reason": "target_leaf_unmatched",
                "node_type": leaf.node.node_type,
                "value": leaf.text,
            }
            for leaf in target_leaves
            if leaf.key not in used_target_ids
        )
        structure_labels = _structure_labels(tree, node_alignments)
        relation_labels = _relation_labels(tree, node_alignments)
        stats = _stats(
            pdf_nodes,
            target_leaves,
            node_alignments,
            relation_labels,
            structure_labels,
            ignored_pdf_nodes,
            unmatched_target_nodes,
        )
        if stats["hard_alignment_rate"] < 0.70:
            warnings.add("alignment_low_hard_coverage")
        if unmatched_target_nodes:
            warnings.add("alignment_unmatched_target_nodes")
        return TinyBDAlignmentResult(
            row_id=row_id,
            input_hash=_stable_hash(
                {
                    "graph_input_hash": graph_row.get("input_hash", ""),
                    "target_input_hash": target_row.get("input_hash", ""),
                    "target_tree_hash": target_payload.get("stable_hash", ""),
                    "alignment_version": ALIGNMENT_BUILDER_VERSION,
                }
            ),
            alignment_version=ALIGNMENT_BUILDER_VERSION,
            node_alignments=tuple(node_alignments),
            relation_labels=tuple(relation_labels),
            structure_labels=tuple(structure_labels),
            ignored_pdf_nodes=ignored_pdf_nodes,
            unmatched_target_nodes=unmatched_target_nodes,
            warnings=tuple(sorted(warnings)),
            stats=stats,
        )

    def align_rows(
        self,
        graph_rows: list[dict[str, Any]],
        target_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        targets_by_row_id = {str(row.get("row_id", "") or ""): row for row in target_rows}
        output: list[dict[str, Any]] = []
        warnings: Counter[str] = Counter()
        relation_counts: Counter[str] = Counter()
        structure_counts: Counter[str] = Counter()
        hard_rates: list[float] = []
        for graph_row in graph_rows:
            row_id = str(graph_row.get("row_id", "") or "")
            target_row = targets_by_row_id.get(row_id)
            if target_row is None:
                result = _empty_result(row_id, graph_row, {}, "target_row_missing")
            else:
                result = self.align_row(graph_row, target_row)
            payload = result.to_json()
            output.append(payload)
            warnings.update(str(item) for item in result.warnings if item)
            relation_counts.update(str(item.get("relation", "")) for item in payload.get("relation_labels", []) if item)
            structure_counts.update(str(item.get("role", "")) for item in payload.get("structure_labels", []) if item)
            hard_rates.append(float(payload.get("stats", {}).get("hard_alignment_rate", 0.0)))
        manifest = {
            "schema_version": "tinybdmath_alignment_manifest_v1",
            "alignment_version": ALIGNMENT_BUILDER_VERSION,
            "rows": len(output),
            "rows_with_hard_labels": sum(1 for item in output if item.get("relation_labels")),
            "rows_with_structure_labels": sum(1 for item in output if item.get("structure_labels")),
            "avg_hard_alignment_rate": round(sum(hard_rates) / len(hard_rates), 6) if hard_rates else 0.0,
            "warnings": dict(sorted(warnings.items())),
            "relation_counts": dict(sorted(relation_counts.items())),
            "structure_counts": dict(sorted(structure_counts.items())),
            "notes": [
                "Alignment labels are training/audit supervision.",
                "Low-confidence or unmatched nodes are ignore labels, not decoder repairs.",
            ],
        }
        return output, manifest


@dataclass(frozen=True)
class _TargetLeafUnit:
    node: CSLTNode
    text: str
    order: int
    key: str = ""
    aliases: tuple[str, ...] = ()


def _pdf_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in row.get("glyph_nodes", []) or []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id", "") or "")
        if not node_id:
            continue
        text = str(item.get("unicode", "") or item.get("text", "") or "")
        bbox = item.get("bbox", [0.0, 0.0, 0.0, 0.0])
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            bbox = [0.0, 0.0, 0.0, 0.0]
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "glyph",
                "text": text,
                "latex": str(item.get("latex", "") or text),
                "bbox": [float(value or 0.0) for value in bbox],
                "font": str(item.get("font", "") or ""),
                "is_math_font": bool(item.get("is_math_font", False)),
            }
        )
    return sorted(nodes, key=lambda item: (float(item["bbox"][0]), float(item["bbox"][1]), str(item["node_id"])))


def _target_leaf_units(tree: CSLTTree) -> list[_TargetLeafUnit]:
    node_by_id = tree.node_map()
    children_by_source: dict[str, list[CSLTEdge]] = defaultdict(list)
    for edge in tree.edges:
        children_by_source[edge.source].append(edge)
    output: list[_TargetLeafUnit] = []

    def visit(node_id: str) -> None:
        node = node_by_id.get(node_id)
        if node is None:
            return
        if node.node_type == "symbol":
            output.append(
                _TargetLeafUnit(
                    node=node,
                    text=node.value or node.latex,
                    order=len(output),
                    key=node.node_id,
                    aliases=_identity_aliases(node),
                )
            )
            return
        if node.node_type == "text_run":
            for index, char in enumerate(str(node.value or "")):
                output.append(_TargetLeafUnit(node=node, text=char, order=len(output), key=f"{node.node_id}:{index}"))
            return
        if node.node_type == "radical":
            output.append(_TargetLeafUnit(node=node, text=r"\sqrt", order=len(output), key=node.node_id))
        if node.node_type == "artifact":
            return
        for edge in sorted(children_by_source.get(node_id, []), key=lambda item: (item.order, item.relation, item.target)):
            visit(edge.target)

    visit(tree.root_id)
    return output


def _align_leaf_units(
    pdf_nodes: list[dict[str, Any]],
    target_leaves: list[_TargetLeafUnit],
) -> tuple[list[TinyBDNodeAlignment], set[str], set[str], set[str]]:
    alignments: list[TinyBDNodeAlignment] = []
    used_pdf_ids: set[str] = set()
    used_target_ids: set[str] = set()
    warnings: set[str] = set()
    pdf_index = 0
    for leaf in target_leaves:
        target_text = _normalize_symbol_text(leaf.text)
        if not target_text:
            continue
        best_index = -1
        best_score = 0.0
        for index in range(pdf_index, len(pdf_nodes)):
            pdf_node = pdf_nodes[index]
            if pdf_node["node_id"] in used_pdf_ids:
                continue
            score = _match_score(
                target_text,
                str(pdf_node.get("text", "") or ""),
                str(pdf_node.get("latex", "") or ""),
                leaf.aliases,
            )
            order_penalty = min(0.20, max(0, index - pdf_index) * 0.02)
            score -= order_penalty
            if score > best_score:
                best_score = score
                best_index = index
            if score >= 0.98:
                break
        if best_index < 0 or best_score < 0.45:
            continue
        pdf_node = pdf_nodes[best_index]
        confidence = round(max(0.0, min(1.0, best_score)), 6)
        label = "hard" if confidence >= 0.90 else "soft"
        alignments.append(
            TinyBDNodeAlignment(
                pdf_node_id=str(pdf_node["node_id"]),
                target_node_id=leaf.node.node_id,
                confidence=confidence,
                label=label,
                reason="symbol_text_order_match" if label == "hard" else "weak_symbol_text_order_match",
                target_key=leaf.key,
                target_node_type=leaf.node.node_type,
                target_value=leaf.node.value,
                target_latex=leaf.node.latex,
                target_attrs=dict(leaf.node.attrs or {}),
            )
        )
        used_pdf_ids.add(str(pdf_node["node_id"]))
        used_target_ids.add(leaf.key)
        pdf_index = max(pdf_index, best_index + 1)
    if not alignments and target_leaves:
        warnings.add("alignment_no_leaf_matches")
    return alignments, used_pdf_ids, used_target_ids, warnings


def _relation_labels(tree: CSLTTree, node_alignments: list[TinyBDNodeAlignment]) -> list[TinyBDRelationLabel]:
    pdf_by_target: defaultdict[str, list[TinyBDNodeAlignment]] = defaultdict(list)
    for item in node_alignments:
        if item.label in {"hard", "soft"}:
            pdf_by_target[item.target_node_id].append(item)
    leaves_by_subtree = _aligned_leaves_by_subtree(tree, pdf_by_target)
    labels: list[TinyBDRelationLabel] = []
    for edge in tree.edges:
        relation = STRUCTURE_RELATION_TO_LABEL.get(edge.relation)
        if relation is None:
            continue
        parent_leaf = _source_anchor_leaf(tree, edge, leaves_by_subtree)
        child_leaf = _representative_leaf(edge.target, leaves_by_subtree)
        if parent_leaf is None or child_leaf is None:
            continue
        if parent_leaf.pdf_node_id == child_leaf.pdf_node_id:
            continue
        confidence = min(parent_leaf.confidence, child_leaf.confidence)
        labels.append(
            TinyBDRelationLabel(
                source=parent_leaf.pdf_node_id,
                target=child_leaf.pdf_node_id,
                relation=relation,
                confidence=round(confidence, 6),
                supervision="hard" if parent_leaf.label == "hard" and child_leaf.label == "hard" else "soft",
                target_edge=edge.to_json(),
            )
        )
    labels.extend(_sequence_next_labels(tree, leaves_by_subtree))
    return _dedupe_relation_labels(labels)


def _structure_labels(tree: CSLTTree, node_alignments: list[TinyBDNodeAlignment]) -> list[dict[str, Any]]:
    pdf_by_target: defaultdict[str, list[TinyBDNodeAlignment]] = defaultdict(list)
    for item in node_alignments:
        if item.label in {"hard", "soft"}:
            pdf_by_target[item.target_node_id].append(item)
    leaves_by_subtree = _aligned_leaves_by_subtree(tree, pdf_by_target)
    labels: list[dict[str, Any]] = []
    for node in tree.nodes:
        if node.node_type == "fraction":
            numerator = _structure_child_leaves(tree, node.node_id, "numerator", leaves_by_subtree)
            denominator = _structure_child_leaves(tree, node.node_id, "denominator", leaves_by_subtree)
            if numerator and denominator:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_FRACTION_SEPARATOR_EVIDENCE",
                        "above_pdf_node_ids": _pdf_ids(numerator),
                        "below_pdf_node_ids": _pdf_ids(denominator),
                        "confidence": _structure_confidence(numerator + denominator),
                        "supervision": _structure_supervision(numerator + denominator),
                    }
                )
        elif node.node_type == "radical":
            body = _structure_child_leaves(tree, node.node_id, "radical_body", leaves_by_subtree)
            radical_marks = [
                item
                for item in leaves_by_subtree.get(node.node_id, [])
                if item.target_node_id == node.node_id
            ]
            if body:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_RADICAL_MARK_EVIDENCE",
                        "body_pdf_node_ids": _pdf_ids(body),
                        "mark_pdf_node_ids": _pdf_ids(radical_marks),
                        "confidence": _structure_confidence(body + radical_marks),
                        "supervision": _structure_supervision(body + radical_marks),
                    }
                )
    return labels


def _structure_child_leaves(
    tree: CSLTTree,
    source: str,
    relation: str,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> list[TinyBDNodeAlignment]:
    output: list[TinyBDNodeAlignment] = []
    for edge in tree.edges:
        if edge.source == source and edge.relation == relation:
            output.extend(leaves_by_subtree.get(edge.target, []))
    return output


def _pdf_ids(items: list[TinyBDNodeAlignment]) -> list[str]:
    return sorted({item.pdf_node_id for item in items})


def _structure_confidence(items: list[TinyBDNodeAlignment]) -> float:
    if not items:
        return 0.0
    return round(min(item.confidence for item in items), 6)


def _structure_supervision(items: list[TinyBDNodeAlignment]) -> str:
    if not items:
        return "missing"
    return "hard" if all(item.label == "hard" for item in items) else "soft"


def _source_anchor_leaf(
    tree: CSLTTree,
    edge: CSLTEdge,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> TinyBDNodeAlignment | None:
    node_by_id = tree.node_map()
    source_node = node_by_id.get(edge.source)
    if source_node is not None and source_node.node_type == "script" and edge.relation in {"sub", "sup"}:
        base_edges = [
            item
            for item in tree.edges
            if item.source == edge.source and item.relation == "base"
        ]
        if base_edges:
            return _representative_leaf(base_edges[0].target, leaves_by_subtree)
    return _representative_leaf(edge.source, leaves_by_subtree)


def _aligned_leaves_by_subtree(
    tree: CSLTTree,
    pdf_by_target: dict[str, list[TinyBDNodeAlignment]],
) -> dict[str, list[TinyBDNodeAlignment]]:
    node_by_id = tree.node_map()
    children: dict[str, list[str]] = defaultdict(list)
    for edge in sorted(tree.edges, key=lambda item: (item.source, item.order, item.relation, item.target)):
        children[edge.source].append(edge.target)
    memo: dict[str, list[TinyBDNodeAlignment]] = {}

    def collect(node_id: str) -> list[TinyBDNodeAlignment]:
        if node_id in memo:
            return memo[node_id]
        result: list[TinyBDNodeAlignment] = []
        if node_id in pdf_by_target:
            result.extend(pdf_by_target[node_id])
        for child in children.get(node_id, []):
            if child in node_by_id:
                result.extend(collect(child))
        memo[node_id] = result
        return result

    for node_id in node_by_id:
        collect(node_id)
    return memo


def _representative_leaf(
    node_id: str,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> TinyBDNodeAlignment | None:
    leaves = leaves_by_subtree.get(node_id, [])
    if not leaves:
        return None
    return sorted(leaves, key=lambda item: (-item.confidence, item.pdf_node_id))[0]


def _sequence_next_labels(
    tree: CSLTTree,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> list[TinyBDRelationLabel]:
    labels: list[TinyBDRelationLabel] = []
    sequence_relations = {"child", "next", "cell_content"}
    for source in sorted({edge.source for edge in tree.edges}):
        children = [
            edge
            for edge in tree.edges
            if edge.source == source and edge.relation in sequence_relations
        ]
        representatives = [
            _representative_leaf(edge.target, leaves_by_subtree)
            for edge in sorted(children, key=lambda item: (item.order, item.relation, item.target))
        ]
        representatives = [item for item in representatives if item is not None]
        for previous, current in zip(representatives, representatives[1:]):
            if previous.pdf_node_id == current.pdf_node_id:
                continue
            labels.append(
                TinyBDRelationLabel(
                    source=previous.pdf_node_id,
                    target=current.pdf_node_id,
                    relation="NEXT",
                    confidence=round(min(previous.confidence, current.confidence), 6),
                    supervision="hard" if previous.label == "hard" and current.label == "hard" else "soft",
                    target_edge={"relation": "next", "source": previous.target_node_id, "target": current.target_node_id},
                )
            )
    return labels


def _dedupe_relation_labels(labels: list[TinyBDRelationLabel]) -> list[TinyBDRelationLabel]:
    best: dict[tuple[str, str, str], TinyBDRelationLabel] = {}
    for label in labels:
        key = (label.source, label.target, label.relation)
        current = best.get(key)
        if current is None or label.confidence > current.confidence:
            best[key] = label
    return sorted(best.values(), key=lambda item: (item.source, item.target, item.relation))


def _match_score(target: str, pdf_text: str, pdf_latex: str, target_aliases: tuple[str, ...] = ()) -> float:
    target_norm = _normalize_symbol_text(target)
    pdf_norm = _normalize_symbol_text(pdf_text)
    pdf_latex_norm = _normalize_symbol_text(pdf_latex)
    if not target_norm:
        return 0.0
    if _SYMBOL_EQUIVALENCE.equivalent(target_norm, pdf_norm, pdf_latex_norm, target_aliases):
        return 1.0
    if target_norm in {pdf_norm, pdf_latex_norm}:
        return 1.0
    if len(target_norm) > 1 and pdf_norm and target_norm.startswith(pdf_norm):
        return 0.72
    if len(pdf_norm) > 1 and target_norm and pdf_norm.startswith(target_norm):
        return 0.68
    if target_norm.lower() in {pdf_norm.lower(), pdf_latex_norm.lower()}:
        return 0.88
    return 0.0


def _normalize_symbol_text(text: str) -> str:
    return str(text or "").strip()


def _identity_aliases(node: CSLTNode) -> tuple[str, ...]:
    value = node.attrs.get("identity_aliases", ()) if isinstance(node.attrs, dict) else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item or ""))
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _ignore_reason(node: dict[str, Any]) -> str:
    text = str(node.get("text", "") or "")
    if not text.strip():
        return "spacing_or_blank"
    if _is_punctuation_artifact(text):
        return "unmatched_punctuation_or_marker"
    return "unmatched_pdf_node"


def _is_punctuation_artifact(text: str) -> bool:
    return str(text or "") in {"|", "·", " ", "\u200b"}


def _stats(
    pdf_nodes: list[dict[str, Any]],
    target_leaves: list[_TargetLeafUnit],
    alignments: list[TinyBDNodeAlignment],
    relation_labels: list[TinyBDRelationLabel],
    structure_labels: list[dict[str, Any]],
    ignored_pdf_nodes: tuple[dict[str, Any], ...],
    unmatched_target_nodes: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    hard = sum(1 for item in alignments if item.label == "hard")
    soft = sum(1 for item in alignments if item.label == "soft")
    target_count = len(target_leaves)
    pdf_count = len(pdf_nodes)
    relation_counter = Counter(item.relation for item in relation_labels)
    return {
        "pdf_nodes": pdf_count,
        "target_leaves": target_count,
        "hard_node_alignments": hard,
        "soft_node_alignments": soft,
        "ignored_pdf_nodes": len(ignored_pdf_nodes),
        "unmatched_target_nodes": len(unmatched_target_nodes),
        "hard_alignment_rate": round(hard / target_count, 6) if target_count else 0.0,
        "leaf_alignment_rate": round((hard + soft) / target_count, 6) if target_count else 0.0,
        "pdf_coverage_rate": round((hard + soft) / pdf_count, 6) if pdf_count else 0.0,
        "relation_labels": len(relation_labels),
        "structure_labels": len(structure_labels),
        "relation_counts": dict(sorted(relation_counter.items())),
    }


def _empty_result(
    row_id: str,
    graph_row: dict[str, Any],
    target_row: dict[str, Any],
    warning: str,
) -> TinyBDAlignmentResult:
    return TinyBDAlignmentResult(
        row_id=row_id,
        input_hash=_stable_hash(
            {
                "graph_input_hash": graph_row.get("input_hash", ""),
                "target_input_hash": target_row.get("input_hash", ""),
                "alignment_version": ALIGNMENT_BUILDER_VERSION,
                "warning": warning,
            }
        ),
        alignment_version=ALIGNMENT_BUILDER_VERSION,
        node_alignments=(),
        relation_labels=(),
        structure_labels=(),
        ignored_pdf_nodes=(),
        unmatched_target_nodes=(),
        warnings=(warning,),
        stats={
            "pdf_nodes": len(_pdf_nodes(graph_row)),
            "target_leaves": 0,
            "hard_node_alignments": 0,
            "soft_node_alignments": 0,
            "hard_alignment_rate": 0.0,
            "leaf_alignment_rate": 0.0,
            "pdf_coverage_rate": 0.0,
            "relation_labels": 0,
            "structure_labels": 0,
        },
    )


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8", errors="ignore")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return 0.0
    if hasattr(value, "to_json"):
        return value.to_json()
    return str(value)
