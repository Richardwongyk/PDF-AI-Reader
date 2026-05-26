"""Candidate-only SLT-to-LaTeX decoder for TinyBDMath evidence.

The decoder consumes generic glyph facts plus selected relation edges.  It is
not a handwritten paper-specific recognizer; it only turns already-predicted
relations into a conservative LaTeX candidate and records warnings whenever the
structure is incomplete or ambiguous.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any


TINYBDMATH_LATEX_DECODER_VERSION = "tinybdmath_slt_latex_candidate_v2_relation_only_radical"


@dataclass(frozen=True)
class TinyBDDecodedLatex:
    decoder_version: str
    latex: str
    confidence: float
    warnings: tuple[str, ...]
    candidate_only: bool = True
    accepted: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def decode_latex_candidate(
    glyphs: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    *,
    vectors: list[dict[str, Any]] | None = None,
    fallback_text: str = "",
) -> TinyBDDecodedLatex:
    glyph_by_id = {_node_id(glyph): glyph for glyph in glyphs if _node_id(glyph)}
    vector_by_id = {_node_id(vector): vector for vector in vectors or [] if _node_id(vector)}
    node_by_id: dict[str, dict[str, Any]] = {**glyph_by_id, **vector_by_id}
    relations = [
        relation
        for relation in structural_candidate.get("selected_relations", [])
        if isinstance(relation, dict)
    ]
    warnings = set(str(item) for item in structural_candidate.get("verifier_warnings", []) if item)
    if not glyph_by_id:
        return _fallback(fallback_text, warnings | {"decoder_no_glyphs"})
    if not relations:
        return _fallback(fallback_text, warnings | {"decoder_no_selected_relations"})

    by_source: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    has_parent: set[str] = set()
    for relation in relations:
        source = str(relation.get("source", "") or "")
        target = str(relation.get("target", "") or "")
        kind = str(relation.get("relation", "") or "")
        if source not in node_by_id or target not in node_by_id:
            warnings.add("decoder_relation_references_missing_glyph")
            continue
        by_source[source][kind].append(relation)
        if kind in {"HORIZONTAL", "SUP", "SUB", "ABOVE", "BELOW", "RADICAL_BODY"}:
            has_parent.add(target)
        if kind in {"FRACTION_BAR", "OVERLINE"} and source == target:
            has_parent.add(source)

    consumed_by_rule = _rule_consumed_nodes(by_source, node_by_id)
    roots = [node_id for node_id in glyph_by_id if node_id not in has_parent and node_id not in consumed_by_rule]
    if not roots:
        roots = list(glyph_by_id)
        warnings.add("decoder_no_root")
    ordered_roots = sorted(roots, key=lambda node_id: _glyph_sort_key(glyph_by_id[node_id]))
    parts: list[str] = []
    visited: set[str] = set()
    rule_parts = _decode_rule_structures(by_source, node_by_id, visited, warnings)
    parts.extend(rule_parts)
    for root in ordered_roots:
        text = _decode_node(root, node_by_id, by_source, visited, warnings)
        if text:
            parts.append(text)
    latex = " ".join(_compact_part(part) for part in parts if part).strip()
    if not latex:
        return _fallback(fallback_text, warnings | {"decoder_empty_output"})
    confidence = _decode_confidence(relations, warnings)
    return TinyBDDecodedLatex(
        decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
        latex=latex,
        confidence=confidence,
        warnings=tuple(sorted(warnings)),
    )


def _decode_node(
    node_id: str,
    node_by_id: dict[str, dict[str, Any]],
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    visited: set[str],
    warnings: set[str],
) -> str:
    if node_id in visited:
        warnings.add("decoder_cycle")
        return ""
    visited.add(node_id)
    base = _glyph_latex(node_by_id[node_id])
    children = by_source.get(node_id, {})
    radical = _first_child(children.get("RADICAL_BODY", []), node_by_id)
    if base in {r"\sqrt", "√", "sqrt"}:
        if radical:
            radical_text = _decode_node(str(radical.get("target", "")), node_by_id, by_source, visited, warnings)
            base = rf"\sqrt{{{radical_text}}}" if radical_text else r"\sqrt{}"
            return base
        warnings.add("decoder_radical_missing_body_relation")
        base = r"\sqrt{}"
    sup = _first_child(children.get("SUP", []), node_by_id)
    sub = _first_child(children.get("SUB", []), node_by_id)
    if sub:
        sub_text = _decode_node(str(sub.get("target", "")), node_by_id, by_source, visited, warnings)
        if sub_text:
            base = f"{base}_{{{sub_text}}}"
    if sup:
        sup_text = _decode_node(str(sup.get("target", "")), node_by_id, by_source, visited, warnings)
        if sup_text:
            base = f"{base}^{{{sup_text}}}"
    right = _first_child(children.get("HORIZONTAL", []), node_by_id)
    if right:
        right_text = _decode_node(str(right.get("target", "")), node_by_id, by_source, visited, warnings)
        if right_text:
            base = f"{base}{right_text}"
    return base


def _decode_rule_structures(
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
    visited: set[str],
    warnings: set[str],
) -> list[str]:
    parts: list[str] = []
    for source in sorted(by_source, key=lambda node_id: _glyph_sort_key(node_by_id.get(node_id, {}))):
        children = by_source[source]
        if children.get("FRACTION_BAR"):
            above = _ordered_children(children.get("ABOVE", []), node_by_id)
            below = _ordered_children(children.get("BELOW", []), node_by_id)
            if above and below:
                numerator = _decode_group([str(item.get("target", "")) for item in above], node_by_id, by_source, visited, warnings)
                denominator = _decode_group([str(item.get("target", "")) for item in below], node_by_id, by_source, visited, warnings)
                parts.append(rf"\frac{{{numerator}}}{{{denominator}}}")
            else:
                warnings.add("decoder_fraction_missing_group")
        elif children.get("OVERLINE"):
            above = _ordered_children(children.get("ABOVE", []), node_by_id)
            if above:
                body = _decode_group([str(item.get("target", "")) for item in above], node_by_id, by_source, visited, warnings)
                parts.append(rf"\overline{{{body}}}")
            else:
                warnings.add("decoder_overline_missing_group")
    return parts


def _decode_group(
    node_ids: list[str],
    node_by_id: dict[str, dict[str, Any]],
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    visited: set[str],
    warnings: set[str],
) -> str:
    local_visited = set(visited)
    parts: list[str] = []
    for node_id in sorted([node_id for node_id in node_ids if node_id in node_by_id], key=lambda item: _glyph_sort_key(node_by_id[item])):
        if node_id in visited:
            continue
        text = _decode_node(node_id, node_by_id, by_source, local_visited, warnings)
        if text:
            parts.append(text)
            visited.add(node_id)
    return "".join(parts)


def _rule_consumed_nodes(
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
) -> set[str]:
    consumed: set[str] = set()
    for children in by_source.values():
        if not (children.get("FRACTION_BAR") or children.get("OVERLINE")):
            continue
        for relation in children.get("ABOVE", []) + children.get("BELOW", []):
            target = str(relation.get("target", "") or "")
            if target in node_by_id:
                consumed.add(target)
    return consumed


def _first_child(relations: list[dict[str, Any]], node_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    valid = [relation for relation in relations if str(relation.get("target", "")) in node_by_id]
    if not valid:
        return None
    return sorted(valid, key=lambda relation: _glyph_sort_key(node_by_id[str(relation.get("target", ""))]))[0]


def _ordered_children(relations: list[dict[str, Any]], node_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [relation for relation in relations if str(relation.get("target", "")) in node_by_id],
        key=lambda relation: _glyph_sort_key(node_by_id[str(relation.get("target", ""))]),
    )


def _glyph_latex(glyph: dict[str, Any]) -> str:
    latex = str(glyph.get("latex", "") or "")
    if latex:
        return latex
    unicode_value = str(glyph.get("unicode", "") or "")
    if unicode_value:
        return unicode_value
    raw = glyph.get("raw", {}) if isinstance(glyph.get("raw", {}), dict) else {}
    return str(raw.get("text", "") or "")


def _glyph_sort_key(glyph: dict[str, Any]) -> tuple[float, float, str]:
    bbox = glyph.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raw = glyph.get("raw", {}) if isinstance(glyph.get("raw", {}), dict) else {}
        bbox = raw.get("bbox", [0.0, 0.0, 0.0, 0.0])
    try:
        return (float(bbox[0]), float(bbox[1]), _node_id(glyph))
    except (TypeError, ValueError):
        return (0.0, 0.0, _node_id(glyph))


def _node_id(glyph: dict[str, Any]) -> str:
    return str(glyph.get("node_id", "") or glyph.get("id", "") or "")


def _compact_part(text: str) -> str:
    return text.replace(" ", "")


def _fallback(fallback_text: str, warnings: set[str]) -> TinyBDDecodedLatex:
    return TinyBDDecodedLatex(
        decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
        latex=str(fallback_text or ""),
        confidence=0.0,
        warnings=tuple(sorted(warnings)),
    )


def _decode_confidence(relations: list[dict[str, Any]], warnings: set[str]) -> float:
    if not relations:
        return 0.0
    avg = sum(_float(relation.get("confidence")) for relation in relations) / len(relations)
    penalty = 0.2 if any(warning.startswith("decoder_") for warning in warnings) else 0.0
    return round(max(0.0, min(1.0, avg - penalty)), 6)


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
