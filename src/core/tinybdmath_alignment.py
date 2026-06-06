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
ALIGNMENT_BUILDER_VERSION = "tinybdmath_pdf_graph_to_cslt_alignment_m2"

SEMANTIC_NODE_TYPES = {"symbol", "text_run"}
STRUCTURE_RELATION_TO_LABEL = {
    "base": "BASE",
    "sub": "SUB",
    "sup": "SUP",
    "pre_sub": "PRE_SUB",
    "pre_sup": "PRE_SUP",
    "under": "UNDER",
    "over": "OVER",
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
    pdf_text: str = ""
    pdf_latex: str = ""
    pdf_font: str = ""
    pdf_bbox: list[float] = field(default_factory=list)
    identity_evidence: dict[str, Any] = field(default_factory=dict)

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
        vector_nodes = _pdf_vector_nodes(graph_row)
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
        structure_labels = _structure_labels(tree, node_alignments, vector_nodes=vector_nodes)
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
        vector_role_counts: Counter[str] = Counter()
        hard_rates: list[float] = []
        rows_with_group_boundary = 0
        rows_with_text_or_operator_run = 0
        rows_with_vector_role = 0
        rows_with_identity_evidence = 0
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
            structure_labels = [item for item in payload.get("structure_labels", []) if isinstance(item, dict)]
            structure_counts.update(str(item.get("role", "")) for item in structure_labels if item)
            vector_role_counts.update(
                str(item.get("vector_role", "") or "")
                for item in structure_labels
                if str(item.get("role", "") or "") == "TARGET_VECTOR_ROLE_EVIDENCE"
            )
            structure_roles = {str(item.get("role", "") or "") for item in structure_labels}
            if any("GROUP_BOUNDARY" in role for role in structure_roles):
                rows_with_group_boundary += 1
            if structure_roles.intersection({"TARGET_TEXT_RUN_EVIDENCE", "TARGET_OPERATOR_TEXT_RUN_EVIDENCE"}):
                rows_with_text_or_operator_run += 1
            if "TARGET_VECTOR_ROLE_EVIDENCE" in structure_roles:
                rows_with_vector_role += 1
            if "TARGET_IDENTITY_REPAIR_EVIDENCE" in structure_roles:
                rows_with_identity_evidence += 1
            hard_rates.append(float(payload.get("stats", {}).get("hard_alignment_rate", 0.0)))
        manifest = {
            "schema_version": "tinybdmath_alignment_manifest_v1",
            "alignment_version": ALIGNMENT_BUILDER_VERSION,
            "rows": len(output),
            "rows_with_hard_labels": sum(1 for item in output if item.get("relation_labels")),
            "rows_with_structure_labels": sum(1 for item in output if item.get("structure_labels")),
            "rows_with_group_boundary": rows_with_group_boundary,
            "rows_with_text_or_operator_run": rows_with_text_or_operator_run,
            "rows_with_vector_role": rows_with_vector_role,
            "rows_with_identity_evidence": rows_with_identity_evidence,
            "avg_hard_alignment_rate": round(sum(hard_rates) / len(hard_rates), 6) if hard_rates else 0.0,
            "warnings": dict(sorted(warnings.items())),
            "relation_counts": dict(sorted(relation_counts.items())),
            "structure_counts": dict(sorted(structure_counts.items())),
            "vector_role_counts": dict(sorted(vector_role_counts.items())),
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


def _pdf_vector_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, item in enumerate(row.get("vector_nodes", []) or []):
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id", "") or f"v{index:04d}")
        bbox = item.get("bbox", [0.0, 0.0, 0.0, 0.0])
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            bbox = [0.0, 0.0, 0.0, 0.0]
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "vector",
                "bbox": [float(value or 0.0) for value in bbox],
                "vector_type": str(item.get("vector_type", "") or item.get("type", "") or item.get("kind", "") or "vector"),
                "is_horizontal_rule_candidate": bool(item.get("is_horizontal_rule_candidate", False)),
                "is_vertical_rule_candidate": bool(item.get("is_vertical_rule_candidate", False)),
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
        if node.node_type == "equation_number":
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
        pdf_text = str(pdf_node.get("text", "") or "")
        pdf_latex = str(pdf_node.get("latex", "") or "")
        identity_evidence = _alignment_identity_evidence(
            target_text,
            pdf_text,
            pdf_latex,
            leaf.aliases,
        )
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
                pdf_text=pdf_text,
                pdf_latex=pdf_latex,
                pdf_font=str(pdf_node.get("font", "") or ""),
                pdf_bbox=list(pdf_node.get("bbox", []) or []),
                identity_evidence=identity_evidence,
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


def _structure_labels(
    tree: CSLTTree,
    node_alignments: list[TinyBDNodeAlignment],
    *,
    vector_nodes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    pdf_by_target: defaultdict[str, list[TinyBDNodeAlignment]] = defaultdict(list)
    for item in node_alignments:
        if item.label in {"hard", "soft"}:
            pdf_by_target[item.target_node_id].append(item)
    leaves_by_subtree = _aligned_leaves_by_subtree(tree, pdf_by_target)
    labels: list[dict[str, Any]] = []
    labels.extend(_identity_structure_labels(node_alignments))
    labels.extend(_group_boundary_structure_labels(tree, leaves_by_subtree))
    for node in tree.nodes:
        if node.node_type == "text_run":
            text_run = _ordered_alignments_for_target(node_alignments, node.node_id)
            if text_run:
                role = "TARGET_OPERATOR_TEXT_RUN_EVIDENCE" if bool(node.attrs.get("operator")) else "TARGET_TEXT_RUN_EVIDENCE"
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": role,
                        "text_pdf_node_ids": [item.pdf_node_id for item in text_run],
                        "confidence": _structure_confidence(text_run),
                        "supervision": _structure_supervision(text_run),
                    }
                )
        elif node.node_type == "script" and str(node.attrs.get("placement", "") or "") == "left_attachment":
            base = _structure_child_leaves(tree, node.node_id, "base", leaves_by_subtree)
            pre_sub = _structure_child_leaves(tree, node.node_id, "pre_sub", leaves_by_subtree)
            pre_sup = _structure_child_leaves(tree, node.node_id, "pre_sup", leaves_by_subtree)
            observed = base + pre_sub + pre_sup
            if base and (pre_sub or pre_sup):
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_LEFT_ATTACHMENT_EVIDENCE",
                        "base_pdf_node_ids": _pdf_ids(base),
                        "pre_sub_pdf_node_ids": _pdf_ids(pre_sub),
                        "pre_sup_pdf_node_ids": _pdf_ids(pre_sup),
                        "confidence": _structure_confidence(observed),
                        "supervision": _structure_supervision(observed),
                    }
                )
        elif node.node_type == "fraction":
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
            index = _structure_child_leaves(tree, node.node_id, "radical_index", leaves_by_subtree)
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
                        "index_pdf_node_ids": _pdf_ids(index),
                        "mark_pdf_node_ids": _pdf_ids(radical_marks),
                        "confidence": _structure_confidence(body + index + radical_marks),
                        "supervision": _structure_supervision(body + index + radical_marks),
                    }
                )
        elif node.node_type == "under_over":
            base = _structure_child_leaves(tree, node.node_id, "base", leaves_by_subtree)
            under = _structure_child_leaves(tree, node.node_id, "under", leaves_by_subtree)
            over = _structure_child_leaves(tree, node.node_id, "over", leaves_by_subtree)
            observed = base + under + over
            if base and (under or over):
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_UNDER_OVER_EVIDENCE",
                        "base_pdf_node_ids": _pdf_ids(base),
                        "under_pdf_node_ids": _pdf_ids(under),
                        "over_pdf_node_ids": _pdf_ids(over),
                        "confidence": _structure_confidence(observed),
                        "supervision": _structure_supervision(observed),
                    }
                )
        elif node.node_type == "accent":
            base = _structure_child_leaves(tree, node.node_id, "accent_base", leaves_by_subtree)
            if base:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_ACCENT_ANNOTATION_EVIDENCE",
                        "base_pdf_node_ids": _pdf_ids(base),
                        "annotation_position": str(node.attrs.get("annotation_position", "") or ""),
                        "confidence": _structure_confidence(base),
                        "supervision": _structure_supervision(base),
                    }
                )
        elif node.node_type == "fence":
            body = _structure_child_leaves(tree, node.node_id, "fence_body", leaves_by_subtree)
            open_delimiter = _structure_child_leaves(tree, node.node_id, "fence_open", leaves_by_subtree)
            close_delimiter = _structure_child_leaves(tree, node.node_id, "fence_close", leaves_by_subtree)
            observed = body + open_delimiter + close_delimiter
            if body:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_FENCE_EVIDENCE",
                        "body_pdf_node_ids": _pdf_ids(body),
                        "open_pdf_node_ids": _pdf_ids(open_delimiter),
                        "close_pdf_node_ids": _pdf_ids(close_delimiter),
                        "confidence": _structure_confidence(observed),
                        "supervision": _structure_supervision(observed),
                    }
                )
        elif node.node_type == "matrix":
            labels.extend(_matrix_structure_labels(tree, node.node_id, leaves_by_subtree))
        elif node.node_type == "group":
            group_role = str(node.attrs.get("role", "") or "")
            if group_role == "operator_body":
                operator_run = [
                    item
                    for item in leaves_by_subtree.get(node.node_id, [])
                    if item.target_node_type != "artifact"
                ]
                if operator_run:
                    labels.append(
                        {
                            "target_node_id": node.node_id,
                            "role": "TARGET_OPERATOR_TEXT_RUN_EVIDENCE",
                            "text_pdf_node_ids": _ordered_pdf_ids(operator_run),
                            "confidence": _structure_confidence(operator_run),
                            "supervision": _structure_supervision(operator_run),
                        }
                    )
            if group_role == "enclosure":
                body = [
                    item
                    for item in _structure_child_leaves(tree, node.node_id, "child", leaves_by_subtree)
                    if item.target_node_type != "artifact"
                ]
                if body:
                    labels.append(
                        {
                            "target_node_id": node.node_id,
                            "role": "TARGET_ENCLOSURE_EVIDENCE",
                            "body_pdf_node_ids": _pdf_ids(body),
                            "confidence": _structure_confidence(body),
                            "supervision": _structure_supervision(body),
                        }
                    )
        elif node.node_type == "equation_number":
            tag = leaves_by_subtree.get(node.node_id, [])
            if tag:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_EQUATION_TAG_EVIDENCE",
                        "tag_pdf_node_ids": _pdf_ids(tag),
                        "confidence": _structure_confidence(tag),
                        "supervision": _structure_supervision(tag),
                    }
                )
    labels.extend(_vector_role_structure_labels(vector_nodes or [], labels, node_alignments))
    return labels


def _matrix_structure_labels(
    tree: CSLTTree,
    matrix_id: str,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    rows = _child_edges(tree, matrix_id, "matrix_row")
    matrix_leaves = leaves_by_subtree.get(matrix_id, [])
    if matrix_leaves:
        labels.append(
            {
                "target_node_id": matrix_id,
                "role": "TARGET_MATRIX_GRID_EVIDENCE",
                "matrix_pdf_node_ids": _pdf_ids(matrix_leaves),
                "row_count": len(rows),
                "confidence": _structure_confidence(matrix_leaves),
                "supervision": _structure_supervision(matrix_leaves),
            }
        )
    for row_index, row_edge in enumerate(rows):
        row_leaves = leaves_by_subtree.get(row_edge.target, [])
        if row_leaves:
            labels.append(
                {
                    "target_node_id": row_edge.target,
                    "role": "TARGET_MATRIX_ROW_EVIDENCE",
                    "row_index": row_index,
                    "row_pdf_node_ids": _pdf_ids(row_leaves),
                    "confidence": _structure_confidence(row_leaves),
                    "supervision": _structure_supervision(row_leaves),
                }
            )
        cells = _child_edges(tree, row_edge.target, "matrix_cell")
        for column_index, cell_edge in enumerate(cells):
            cell_leaves = leaves_by_subtree.get(cell_edge.target, [])
            if cell_leaves:
                labels.append(
                    {
                        "target_node_id": cell_edge.target,
                        "role": "TARGET_MATRIX_CELL_EVIDENCE",
                        "row_index": row_index,
                        "column_index": column_index,
                        "cell_pdf_node_ids": _pdf_ids(cell_leaves),
                        "confidence": _structure_confidence(cell_leaves),
                        "supervision": _structure_supervision(cell_leaves),
                    }
                )
    return labels


def _vector_role_structure_labels(
    vector_nodes: list[dict[str, Any]],
    structure_labels: list[dict[str, Any]],
    node_alignments: list[TinyBDNodeAlignment],
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    bbox_by_id = {
        item.pdf_node_id: list(item.pdf_bbox)
        for item in node_alignments
        if item.pdf_node_id and isinstance(item.pdf_bbox, list) and len(item.pdf_bbox) == 4
    }
    for node in vector_nodes:
        role, confidence = _generic_vector_role(node)
        labels.append(
            {
                "target_node_id": "",
                "role": "TARGET_VECTOR_ROLE_EVIDENCE",
                "vector_pdf_node_id": str(node.get("node_id", "") or ""),
                "vector_role": role,
                "vector_type": str(node.get("vector_type", "") or ""),
                "bbox": list(node.get("bbox", [0.0, 0.0, 0.0, 0.0])),
                "confidence": confidence,
                "supervision": "pdf_evidence",
            }
        )
    for item in structure_labels:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "")
        target_node_id = str(item.get("target_node_id", "") or "")
        confidence = float(item.get("confidence", 1.0) or 1.0)
        if role == "TARGET_FRACTION_SEPARATOR_EVIDENCE":
            above = _bbox_for_pdf_ids(item.get("above_pdf_node_ids", []), bbox_by_id)
            below = _bbox_for_pdf_ids(item.get("below_pdf_node_ids", []), bbox_by_id)
            vector, score = _best_vector_role_match(
                vector_nodes,
                lambda candidate: _fraction_vector_role_score(candidate, above, below),
            )
            if score >= 0.45:
                labels.append(
                    _target_vector_role_label(
                        vector,
                        "FRACTION_BAR",
                        target_node_id=target_node_id,
                        related_structure_role=role,
                        confidence=score * confidence,
                    )
                )
        elif role == "TARGET_RADICAL_MARK_EVIDENCE":
            body = _bbox_for_pdf_ids(item.get("body_pdf_node_ids", []), bbox_by_id)
            vector, score = _best_vector_role_match(
                vector_nodes,
                lambda candidate: _radical_vector_role_score(candidate, body),
            )
            if score >= 0.45:
                labels.append(
                    _target_vector_role_label(
                        vector,
                        "RADICAL_MARK",
                        target_node_id=target_node_id,
                        related_structure_role=role,
                        confidence=score * confidence,
                    )
                )
        elif role == "TARGET_ACCENT_ANNOTATION_EVIDENCE":
            base = _bbox_for_pdf_ids(item.get("base_pdf_node_ids", []), bbox_by_id)
            position = str(item.get("annotation_position", "") or "")
            vector_role = {"over": "OVERLINE", "under": "UNDERLINE"}.get(position)
            if vector_role is None:
                continue
            vector, score = _best_vector_role_match(
                vector_nodes,
                lambda candidate: _annotation_vector_role_score(candidate, base, position=position),
            )
            if score >= 0.45:
                labels.append(
                    _target_vector_role_label(
                        vector,
                        vector_role,
                        target_node_id=target_node_id,
                        related_structure_role=role,
                        confidence=score * confidence,
                    )
                )
        elif role == "TARGET_ENCLOSURE_EVIDENCE":
            body = _bbox_for_pdf_ids(item.get("body_pdf_node_ids", []), bbox_by_id)
            vector, score = _best_vector_role_match(
                vector_nodes,
                lambda candidate: _enclosure_vector_role_score(candidate, body),
            )
            if score >= 0.35:
                labels.append(
                    _target_vector_role_label(
                        vector,
                        "ENCLOSURE_RULE",
                        target_node_id=target_node_id,
                        related_structure_role=role,
                        confidence=score * confidence,
                    )
                )
        elif role == "TARGET_FENCE_EVIDENCE":
            body = _bbox_for_pdf_ids(item.get("body_pdf_node_ids", []), bbox_by_id)
            vector, score = _best_vector_role_match(
                vector_nodes,
                lambda candidate: _fence_vector_role_score(candidate, body),
            )
            if score >= 0.45:
                labels.append(
                    _target_vector_role_label(
                        vector,
                        "FENCE_DELIMITER",
                        target_node_id=target_node_id,
                        related_structure_role=role,
                        confidence=score * confidence,
                    )
                )
        elif role == "TARGET_MATRIX_GRID_EVIDENCE":
            matrix = _bbox_for_pdf_ids(item.get("matrix_pdf_node_ids", []), bbox_by_id)
            vector, score = _best_vector_role_match(
                vector_nodes,
                lambda candidate: _matrix_border_vector_role_score(candidate, matrix),
            )
            if score >= 0.45:
                labels.append(
                    _target_vector_role_label(
                        vector,
                        "MATRIX_BORDER",
                        target_node_id=target_node_id,
                        related_structure_role=role,
                        confidence=score * confidence,
                    )
                )
    return _dedupe_vector_role_labels(labels)


def _target_vector_role_label(
    vector_node: dict[str, Any],
    vector_role: str,
    *,
    target_node_id: str,
    related_structure_role: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "target_node_id": target_node_id,
        "role": "TARGET_VECTOR_ROLE_EVIDENCE",
        "vector_pdf_node_id": str(vector_node.get("node_id", "") or ""),
        "vector_role": vector_role,
        "related_structure_role": related_structure_role,
        "vector_type": str(vector_node.get("vector_type", "") or ""),
        "bbox": list(vector_node.get("bbox", [0.0, 0.0, 0.0, 0.0])),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 6),
        "supervision": "target_pdf_evidence",
    }


def _dedupe_vector_role_labels(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in labels:
        key = (
            str(item.get("target_node_id", "") or ""),
            str(item.get("vector_pdf_node_id", "") or ""),
            str(item.get("vector_role", "") or ""),
            str(item.get("related_structure_role", "") or ""),
        )
        current = best.get(key)
        if current is None or float(item.get("confidence", 0.0) or 0.0) > float(current.get("confidence", 0.0) or 0.0):
            best[key] = item
    return sorted(
        best.values(),
        key=lambda item: (
            str(item.get("vector_pdf_node_id", "") or ""),
            str(item.get("target_node_id", "") or ""),
            str(item.get("vector_role", "") or ""),
            str(item.get("related_structure_role", "") or ""),
        ),
    )


def _bbox_for_pdf_ids(ids: Any, bbox_by_id: dict[str, list[float]]) -> list[float] | None:
    boxes = [
        bbox_by_id[str(item)]
        for item in ids
        if str(item) in bbox_by_id and len(bbox_by_id[str(item)]) == 4
    ] if isinstance(ids, (list, tuple, set)) else []
    if not boxes:
        return None
    return [
        min(float(box[0]) for box in boxes),
        min(float(box[1]) for box in boxes),
        max(float(box[2]) for box in boxes),
        max(float(box[3]) for box in boxes),
    ]


def _best_vector_role_match(
    vector_nodes: list[dict[str, Any]],
    score_fn: Any,
) -> tuple[dict[str, Any], float]:
    best_node: dict[str, Any] = {}
    best_score = 0.0
    for node in vector_nodes:
        score = float(score_fn(node) or 0.0)
        if score > best_score:
            best_node = node
            best_score = score
    return best_node, round(max(0.0, min(1.0, best_score)), 6)


def _fraction_vector_role_score(
    vector: dict[str, Any],
    above: list[float] | None,
    below: list[float] | None,
) -> float:
    if above is None or below is None or not _is_horizontal_vector_bbox(vector):
        return 0.0
    vector_box = _bbox(vector)
    union_x0 = min(float(above[0]), float(below[0]))
    union_x1 = max(float(above[2]), float(below[2]))
    union_width = max(1e-6, union_x1 - union_x0)
    x_overlap = _overlap(vector_box[0], vector_box[2], union_x0, union_x1) / union_width
    above_bottom = float(above[3])
    below_top = float(below[1])
    center_y = (vector_box[1] + vector_box[3]) / 2.0
    midpoint = (above_bottom + below_top) / 2.0
    gap = max(1.0, abs(below_top - above_bottom), _bbox_height(above), _bbox_height(below))
    between = 1.0 if min(above_bottom, below_top) - gap <= center_y <= max(above_bottom, below_top) + gap else 0.0
    midpoint_score = max(0.0, 1.0 - (abs(center_y - midpoint) / gap))
    return (0.45 * _horizontal_shape_score(vector_box)) + (0.35 * x_overlap) + (0.20 * max(between, midpoint_score))


def _radical_vector_role_score(vector: dict[str, Any], body: list[float] | None) -> float:
    if body is None or not _is_horizontal_vector_bbox(vector):
        return 0.0
    vector_box = _bbox(vector)
    x_overlap = _overlap(vector_box[0], vector_box[2], float(body[0]), float(body[2])) / max(1e-6, _bbox_width(body))
    center_y = (vector_box[1] + vector_box[3]) / 2.0
    top_distance = abs(center_y - float(body[1])) / max(1.0, _bbox_height(body))
    y_score = max(0.0, 1.0 - top_distance)
    return (0.45 * _horizontal_shape_score(vector_box)) + (0.35 * x_overlap) + (0.20 * y_score)


def _annotation_vector_role_score(
    vector: dict[str, Any],
    body: list[float] | None,
    *,
    position: str,
) -> float:
    if body is None or not _is_horizontal_vector_bbox(vector):
        return 0.0
    vector_box = _bbox(vector)
    x_overlap = _overlap(vector_box[0], vector_box[2], float(body[0]), float(body[2])) / max(1e-6, _bbox_width(body))
    center_y = (vector_box[1] + vector_box[3]) / 2.0
    reference_y = float(body[1]) if position == "over" else float(body[3])
    y_score = max(0.0, 1.0 - (abs(center_y - reference_y) / max(1.0, _bbox_height(body))))
    return (0.45 * _horizontal_shape_score(vector_box)) + (0.35 * x_overlap) + (0.20 * y_score)


def _enclosure_vector_role_score(vector: dict[str, Any], body: list[float] | None) -> float:
    if body is None:
        return 0.0
    vector_box = _bbox(vector)
    horizontal = _horizontal_shape_score(vector_box)
    vertical = _vertical_shape_score(vector_box)
    if max(horizontal, vertical) <= 0.0:
        return 0.0
    body_width = max(1.0, _bbox_width(body))
    body_height = max(1.0, _bbox_height(body))
    if horizontal >= vertical:
        span = _overlap(vector_box[0], vector_box[2], float(body[0]), float(body[2])) / body_width
        edge_distance = min(abs(vector_box[1] - float(body[1])), abs(vector_box[1] - float(body[3]))) / body_height
    else:
        span = _overlap(vector_box[1], vector_box[3], float(body[1]), float(body[3])) / body_height
        edge_distance = min(abs(vector_box[0] - float(body[0])), abs(vector_box[0] - float(body[2]))) / body_width
    proximity = max(0.0, 1.0 - edge_distance)
    return (0.40 * max(horizontal, vertical)) + (0.35 * span) + (0.25 * proximity)


def _fence_vector_role_score(vector: dict[str, Any], body: list[float] | None) -> float:
    if body is None or not _is_vertical_vector_bbox(vector):
        return 0.0
    vector_box = _bbox(vector)
    y_overlap = _overlap(vector_box[1], vector_box[3], float(body[1]), float(body[3])) / max(1e-6, _bbox_height(body))
    center_x = (vector_box[0] + vector_box[2]) / 2.0
    x_distance = min(abs(center_x - float(body[0])), abs(center_x - float(body[2]))) / max(1.0, _bbox_width(body))
    proximity = max(0.0, 1.0 - x_distance)
    return (0.45 * _vertical_shape_score(vector_box)) + (0.35 * y_overlap) + (0.20 * proximity)


def _matrix_border_vector_role_score(vector: dict[str, Any], matrix: list[float] | None) -> float:
    if matrix is None:
        return 0.0
    vector_box = _bbox(vector)
    horizontal = _horizontal_shape_score(vector_box)
    vertical = _vertical_shape_score(vector_box)
    if max(horizontal, vertical) <= 0.0:
        return 0.0
    if horizontal >= vertical:
        span = _overlap(vector_box[0], vector_box[2], float(matrix[0]), float(matrix[2])) / max(1e-6, _bbox_width(matrix))
        edge_distance = min(abs(vector_box[1] - float(matrix[1])), abs(vector_box[1] - float(matrix[3]))) / max(1.0, _bbox_height(matrix))
        orientation = horizontal
    else:
        span = _overlap(vector_box[1], vector_box[3], float(matrix[1]), float(matrix[3])) / max(1e-6, _bbox_height(matrix))
        edge_distance = min(abs(vector_box[0] - float(matrix[0])), abs(vector_box[0] - float(matrix[2]))) / max(1.0, _bbox_width(matrix))
        orientation = vertical
    proximity = max(0.0, 1.0 - edge_distance)
    return (0.40 * orientation) + (0.35 * span) + (0.25 * proximity)


def _bbox(vector: dict[str, Any]) -> list[float]:
    bbox = vector.get("bbox", [0.0, 0.0, 0.0, 0.0])
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    return [float(value or 0.0) for value in bbox]


def _bbox_width(bbox: list[float]) -> float:
    return max(0.0, float(bbox[2]) - float(bbox[0]))


def _bbox_height(bbox: list[float]) -> float:
    return max(0.0, float(bbox[3]) - float(bbox[1]))


def _is_horizontal_vector_bbox(vector: dict[str, Any]) -> bool:
    return _horizontal_shape_score(_bbox(vector)) > 0.0


def _is_vertical_vector_bbox(vector: dict[str, Any]) -> bool:
    return _vertical_shape_score(_bbox(vector)) > 0.0


def _horizontal_shape_score(bbox: list[float]) -> float:
    width = _bbox_width(bbox)
    height = _bbox_height(bbox)
    if width <= 0.0:
        return 0.0
    aspect = width / max(height, 1e-6)
    return max(0.0, min(1.0, aspect / 6.0))


def _vertical_shape_score(bbox: list[float]) -> float:
    width = _bbox_width(bbox)
    height = _bbox_height(bbox)
    if height <= 0.0:
        return 0.0
    aspect = height / max(width, 1e-6)
    return max(0.0, min(1.0, aspect / 6.0))


def _overlap(first0: float, first1: float, second0: float, second1: float) -> float:
    return max(0.0, min(float(first1), float(second1)) - max(float(first0), float(second0)))


def _identity_structure_labels(node_alignments: list[TinyBDNodeAlignment]) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for item in node_alignments:
        evidence = item.identity_evidence if isinstance(item.identity_evidence, dict) else {}
        if not evidence.get("evidence_sources"):
            continue
        labels.append(
            {
                "target_node_id": item.target_node_id,
                "role": "TARGET_IDENTITY_REPAIR_EVIDENCE",
                "pdf_node_id": item.pdf_node_id,
                "target_node_type": item.target_node_type,
                "target_value": item.target_value,
                "target_latex": item.target_latex,
                "pdf_text": item.pdf_text,
                "pdf_latex": item.pdf_latex,
                "evidence_sources": list(evidence.get("evidence_sources", [])),
                "requires_repair": bool(evidence.get("requires_repair", False)),
                "target_aliases": list(evidence.get("target_aliases", [])),
                "confidence": item.confidence,
                "supervision": item.label,
            }
        )
    return labels


def _group_boundary_structure_labels(
    tree: CSLTTree,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    for node in tree.nodes:
        if node.node_type == "group":
            group_role = str(node.attrs.get("role", "") or "")
            members = [
                item
                for item in leaves_by_subtree.get(node.node_id, [])
                if item.target_node_type != "artifact"
            ]
            if members and group_role != "root":
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_GROUP_BOUNDARY_EVIDENCE",
                        "group_kind": group_role or "group",
                        "member_pdf_node_ids": _ordered_pdf_ids(members),
                        "first_pdf_node_id": _ordered_pdf_ids(members)[0],
                        "last_pdf_node_id": _ordered_pdf_ids(members)[-1],
                        "confidence": _structure_confidence(members),
                        "supervision": _structure_supervision(members),
                    }
                )
        elif node.node_type == "script":
            labels.extend(_script_group_boundary_labels(tree, node.node_id, leaves_by_subtree))
        elif node.node_type == "fraction":
            numerator = _structure_child_leaves(tree, node.node_id, "numerator", leaves_by_subtree)
            denominator = _structure_child_leaves(tree, node.node_id, "denominator", leaves_by_subtree)
            observed = numerator + denominator
            if numerator or denominator:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_FRACTION_GROUP_BOUNDARY_EVIDENCE",
                        "numerator_pdf_node_ids": _ordered_pdf_ids(numerator),
                        "denominator_pdf_node_ids": _ordered_pdf_ids(denominator),
                        "confidence": _structure_confidence(observed),
                        "supervision": _structure_supervision(observed),
                    }
                )
        elif node.node_type == "radical":
            body = _structure_child_leaves(tree, node.node_id, "radical_body", leaves_by_subtree)
            index = _structure_child_leaves(tree, node.node_id, "radical_index", leaves_by_subtree)
            radical_marks = [
                item
                for item in leaves_by_subtree.get(node.node_id, [])
                if item.target_node_id == node.node_id
            ]
            observed = body + index + radical_marks
            if body or index:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_RADICAL_GROUP_BOUNDARY_EVIDENCE",
                        "body_pdf_node_ids": _ordered_pdf_ids(body),
                        "index_pdf_node_ids": _ordered_pdf_ids(index),
                        "mark_pdf_node_ids": _ordered_pdf_ids(radical_marks),
                        "confidence": _structure_confidence(observed),
                        "supervision": _structure_supervision(observed),
                    }
                )
        elif node.node_type == "fence":
            body = _structure_child_leaves(tree, node.node_id, "fence_body", leaves_by_subtree)
            open_delimiter = _structure_child_leaves(tree, node.node_id, "fence_open", leaves_by_subtree)
            close_delimiter = _structure_child_leaves(tree, node.node_id, "fence_close", leaves_by_subtree)
            observed = body + open_delimiter + close_delimiter
            if body:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_FENCE_GROUP_BOUNDARY_EVIDENCE",
                        "body_pdf_node_ids": _ordered_pdf_ids(body),
                        "open_pdf_node_ids": _ordered_pdf_ids(open_delimiter),
                        "close_pdf_node_ids": _ordered_pdf_ids(close_delimiter),
                        "confidence": _structure_confidence(observed),
                        "supervision": _structure_supervision(observed),
                    }
                )
        elif node.node_type == "matrix":
            members = leaves_by_subtree.get(node.node_id, [])
            if members:
                labels.append(
                    {
                        "target_node_id": node.node_id,
                        "role": "TARGET_MATRIX_GROUP_BOUNDARY_EVIDENCE",
                        "matrix_pdf_node_ids": _ordered_pdf_ids(members),
                        "confidence": _structure_confidence(members),
                        "supervision": _structure_supervision(members),
                    }
                )
            rows = _child_edges(tree, node.node_id, "matrix_row")
            for row_index, row_edge in enumerate(rows):
                row_members = leaves_by_subtree.get(row_edge.target, [])
                if row_members:
                    labels.append(
                        {
                            "target_node_id": row_edge.target,
                            "role": "TARGET_MATRIX_ROW_GROUP_BOUNDARY_EVIDENCE",
                            "row_index": row_index,
                            "row_pdf_node_ids": _ordered_pdf_ids(row_members),
                            "confidence": _structure_confidence(row_members),
                            "supervision": _structure_supervision(row_members),
                        }
                    )
                cells = _child_edges(tree, row_edge.target, "matrix_cell")
                for column_index, cell_edge in enumerate(cells):
                    cell_members = leaves_by_subtree.get(cell_edge.target, [])
                    if cell_members:
                        labels.append(
                            {
                                "target_node_id": cell_edge.target,
                                "role": "TARGET_MATRIX_CELL_GROUP_BOUNDARY_EVIDENCE",
                                "row_index": row_index,
                                "column_index": column_index,
                                "cell_pdf_node_ids": _ordered_pdf_ids(cell_members),
                                "confidence": _structure_confidence(cell_members),
                                "supervision": _structure_supervision(cell_members),
                            }
                        )
    return labels


def _script_group_boundary_labels(
    tree: CSLTTree,
    script_id: str,
    leaves_by_subtree: dict[str, list[TinyBDNodeAlignment]],
) -> list[dict[str, Any]]:
    base = _structure_child_leaves(tree, script_id, "base", leaves_by_subtree)
    sub = _structure_child_leaves(tree, script_id, "sub", leaves_by_subtree)
    sup = _structure_child_leaves(tree, script_id, "sup", leaves_by_subtree)
    pre_sub = _structure_child_leaves(tree, script_id, "pre_sub", leaves_by_subtree)
    pre_sup = _structure_child_leaves(tree, script_id, "pre_sup", leaves_by_subtree)
    observed = base + sub + sup + pre_sub + pre_sup
    if not observed:
        return []
    return [
        {
            "target_node_id": script_id,
            "role": "TARGET_SCRIPT_GROUP_BOUNDARY_EVIDENCE",
            "base_pdf_node_ids": _ordered_pdf_ids(base),
            "sub_pdf_node_ids": _ordered_pdf_ids(sub),
            "sup_pdf_node_ids": _ordered_pdf_ids(sup),
            "pre_sub_pdf_node_ids": _ordered_pdf_ids(pre_sub),
            "pre_sup_pdf_node_ids": _ordered_pdf_ids(pre_sup),
            "confidence": _structure_confidence(observed),
            "supervision": _structure_supervision(observed),
        }
    ]


def _child_edges(tree: CSLTTree, source: str, relation: str) -> list[CSLTEdge]:
    return sorted(
        [edge for edge in tree.edges if edge.source == source and edge.relation == relation],
        key=lambda item: (item.order, item.relation, item.target),
    )


def _ordered_alignments_for_target(
    node_alignments: list[TinyBDNodeAlignment],
    target_node_id: str,
) -> list[TinyBDNodeAlignment]:
    return sorted(
        [item for item in node_alignments if item.target_node_id == target_node_id and item.label in {"hard", "soft"}],
        key=lambda item: (_target_key_index(item.target_key), item.pdf_node_id),
    )


def _target_key_index(target_key: str) -> int:
    pieces = str(target_key or "").rsplit(":", 1)
    if len(pieces) != 2:
        return 0
    try:
        return int(pieces[1])
    except ValueError:
        return 0


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


def _ordered_pdf_ids(items: list[TinyBDNodeAlignment]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        node_id = item.pdf_node_id
        if node_id in seen:
            continue
        seen.add(node_id)
        output.append(node_id)
    return output


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
    if source_node is not None and source_node.node_type == "script" and edge.relation in {"sub", "sup", "pre_sub", "pre_sup"}:
        base_edges = [
            item
            for item in tree.edges
            if item.source == edge.source and item.relation == "base"
        ]
        if base_edges:
            return _representative_leaf(base_edges[0].target, leaves_by_subtree)
    if source_node is not None and source_node.node_type == "under_over" and edge.relation in {"under", "over"}:
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


def _alignment_identity_evidence(
    target_text: str,
    pdf_text: str,
    pdf_latex: str,
    target_aliases: tuple[str, ...],
) -> dict[str, Any]:
    target_norm = _normalize_symbol_text(target_text)
    pdf_norm = _normalize_symbol_text(pdf_text)
    pdf_latex_norm = _normalize_symbol_text(pdf_latex)
    aliases = tuple(str(item) for item in target_aliases if str(item or ""))
    sources: list[str] = []
    if aliases:
        sources.append("target_identity_aliases")
    if pdf_latex_norm and pdf_latex_norm != pdf_norm:
        sources.append("pdf_latex_identity")
    if target_norm and pdf_norm and target_norm != pdf_norm:
        sources.append("target_pdf_text_mismatch")
    if target_norm and pdf_latex_norm and target_norm != pdf_latex_norm:
        sources.append("target_pdf_latex_mismatch")
    if not sources:
        return {}
    return {
        "evidence_sources": sorted(set(sources)),
        "requires_repair": bool(target_norm and target_norm not in {pdf_norm, pdf_latex_norm}),
        "target_aliases": list(aliases),
    }


def _generic_vector_role(node: dict[str, Any]) -> tuple[str, float]:
    bbox = node.get("bbox", [0.0, 0.0, 0.0, 0.0])
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return "VECTOR_RULE", 0.25
    x0, y0, x1, y1 = [float(value or 0.0) for value in bbox]
    width = max(0.0, x1 - x0)
    height = max(0.0, y1 - y0)
    if width <= 0.0 and height <= 0.0:
        return "VECTOR_RULE", 0.25
    if bool(node.get("is_horizontal_rule_candidate")):
        return "HORIZONTAL_RULE", 1.0
    if bool(node.get("is_vertical_rule_candidate")):
        return "VERTICAL_RULE", 1.0
    if width / max(height, 1e-6) >= 6.0 and height <= max(width * 0.08, 2.0):
        return "HORIZONTAL_RULE", 1.0
    if height / max(width, 1e-6) >= 6.0 and width <= max(height * 0.08, 2.0):
        return "VERTICAL_RULE", 1.0
    return ("HORIZONTAL_RULE", 0.75) if width >= height else ("VERTICAL_RULE", 0.75)


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
    structure_counter = Counter(str(item.get("role", "") or "") for item in structure_labels if isinstance(item, dict))
    vector_role_counter = Counter(
        str(item.get("vector_role", "") or "")
        for item in structure_labels
        if isinstance(item, dict) and str(item.get("role", "") or "") == "TARGET_VECTOR_ROLE_EVIDENCE"
    )
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
        "structure_counts": dict(sorted(structure_counter.items())),
        "vector_role_counts": dict(sorted(vector_role_counter.items())),
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
