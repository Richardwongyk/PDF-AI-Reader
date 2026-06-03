"""Candidate-only SLT-to-LaTeX decoder for TinyBDMath evidence.

The decoder consumes generic glyph facts plus selected relation edges.  It is
not a handwritten paper-specific recognizer; it only turns already-predicted
relations into a conservative LaTeX candidate and records warnings whenever the
structure is incomplete or ambiguous.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.symbol_identity_repair import latex_for_unicode_text
from src.core.tinybdmath_graph_parser import GRAPH_PARSER_DEFAULT_NODE_FILTER_THRESHOLD
from src.core.tinybdmath_layout_verifier import verify_layout_candidate


TINYBDMATH_LATEX_DECODER_VERSION = "tinybdmath_slt_latex_candidate_v3_layout_verified"
DECODER_SUPPORTED_RELATIONS = {
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
    "ENCLOSURE_BODY",
    "EQUATION_TAG",
}


@dataclass(frozen=True)
class TinyBDDecodedLatex:
    decoder_version: str
    latex: str
    confidence: float
    warnings: tuple[str, ...]
    candidate_only: bool = True
    accepted: bool = False
    abstain: bool = False
    layout_status: str = "review"
    layout_confidence: float = 0.0
    layout_warnings: tuple[str, ...] = ()
    layout_verification: dict[str, Any] = field(default_factory=dict)
    canonical_cslt: dict[str, Any] = field(default_factory=dict)
    n_best_cslt: tuple[dict[str, Any], ...] = ()
    latex_candidates: tuple[dict[str, Any], ...] = ()
    verifier_ranked_candidates: tuple[dict[str, Any], ...] = ()
    manual_review_recommendation: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def decode_latex_candidate(
    glyphs: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    *,
    vectors: list[dict[str, Any]] | None = None,
    fallback_text: str = "",
) -> TinyBDDecodedLatex:
    filtered_node_ids = _model_filtered_node_ids(structural_candidate)
    glyph_by_id = {_node_id(glyph): glyph for glyph in glyphs if _node_id(glyph) and _node_id(glyph) not in filtered_node_ids}
    vector_by_id = {
        _node_id(vector): vector
        for vector in vectors or []
        if _node_id(vector) and _node_id(vector) not in filtered_node_ids
    }
    node_by_id: dict[str, dict[str, Any]] = {**glyph_by_id, **vector_by_id}
    _attach_node_predictions(node_by_id, structural_candidate)
    relations = [
        relation
        for relation in structural_candidate.get("selected_relations", [])
        if isinstance(relation, dict)
        and str(relation.get("source", "") or "") not in filtered_node_ids
        and str(relation.get("target", "") or "") not in filtered_node_ids
    ]
    warnings = set(str(item) for item in structural_candidate.get("verifier_warnings", []) if item)
    if filtered_node_ids:
        warnings.add("decoder_filtered_spacing_nodes")
    if not glyph_by_id:
        return _finalize_decoded(
            _fallback(fallback_text, warnings | {"decoder_no_glyphs"}),
            glyphs=glyphs,
            structural_candidate=structural_candidate,
            vectors=vectors,
        )
    if not relations:
        text = _decode_linear_glyphs(list(glyph_by_id.values()))
        return _finalize_decoded(
            TinyBDDecodedLatex(
                decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
                latex=text or latex_for_unicode_text(fallback_text),
                confidence=0.0,
                warnings=tuple(sorted(warnings | {"decoder_no_selected_relations"})),
            ),
            glyphs=glyphs,
            structural_candidate=structural_candidate,
            vectors=vectors,
        )

    supported_relations: list[dict[str, Any]] = []
    by_source: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    has_parent: set[str] = set()
    for relation in relations:
        source = str(relation.get("source", "") or "")
        target = str(relation.get("target", "") or "")
        kind = str(relation.get("relation", "") or "")
        if kind not in DECODER_SUPPORTED_RELATIONS:
            warnings.add("decoder_unsupported_relation_labels")
            continue
        if source not in node_by_id or target not in node_by_id:
            warnings.add("decoder_relation_references_missing_glyph")
            continue
        if kind == "TEXT_RUN_NEXT" and not _is_forward_reading_relation(source, target, node_by_id):
            warnings.add("decoder_ignored_backward_text_run_next")
            continue
        supported_relations.append(relation)
        by_source[source][kind].append(relation)
        if kind in {"HORIZONTAL", "SUP", "SUB", "PRE_SUP", "PRE_SUB", "UNDER", "OVER", "ABOVE", "BELOW", "RADICAL_BODY", "RADICAL_INDEX", "TEXT_RUN_NEXT", "FENCE_OPEN", "FENCE_CLOSE", "MATRIX_ROW", "MATRIX_CELL", "CELL_CONTENT", "ACCENT_BASE", "ENCLOSURE_BODY", "EQUATION_TAG"}:
            has_parent.add(target)
        if kind in {"FRACTION_BAR", "OVERLINE", "UNDERLINE"} and source == target:
            has_parent.add(source)
    if not supported_relations:
        text = _decode_linear_glyphs(list(glyph_by_id.values()))
        return _finalize_decoded(
            TinyBDDecodedLatex(
                decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
                latex=text or latex_for_unicode_text(fallback_text),
                confidence=0.0,
                warnings=tuple(sorted(warnings | {"decoder_no_supported_relations"})),
            ),
            glyphs=glyphs,
            structural_candidate=structural_candidate,
            vectors=vectors,
        )

    consumed_by_rule = _rule_consumed_nodes(by_source, node_by_id)
    roots = [node_id for node_id in glyph_by_id if node_id not in has_parent and node_id not in consumed_by_rule]
    parts: list[str] = []
    visited: set[str] = set()
    rule_parts = _decode_rule_structures(by_source, node_by_id, visited, warnings)
    parts.extend(rule_parts)
    if not roots and not rule_parts:
        roots = list(glyph_by_id)
        warnings.add("decoder_no_root")
    ordered_roots = sorted(roots, key=lambda node_id: _glyph_sort_key(glyph_by_id[node_id]))
    for root in ordered_roots:
        if root in visited:
            continue
        text = _decode_node(root, node_by_id, by_source, visited, warnings)
        if text:
            parts.append(text)
    latex = "".join(_compact_part(part) for part in parts if part).strip()
    if not latex:
        return _finalize_decoded(
            _fallback(fallback_text, warnings | {"decoder_empty_output"}),
            glyphs=glyphs,
            structural_candidate=structural_candidate,
            vectors=vectors,
        )
    confidence = _decode_confidence(supported_relations, warnings)
    return _finalize_decoded(
        TinyBDDecodedLatex(
            decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
            latex=latex,
            confidence=confidence,
            warnings=tuple(sorted(warnings)),
        ),
        glyphs=glyphs,
        structural_candidate=structural_candidate,
        vectors=vectors,
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
    text_run_ids = _text_run_chain(node_id, node_by_id, by_source, visited, warnings)
    if len(text_run_ids) > 1:
        run_text = "".join(_glyph_plain_text(node_by_id[item]) for item in text_run_ids)
        run_label = _model_text_run_label(text_run_ids[0], node_by_id)
        if run_label == "OPERATOR":
            base = rf"\operatorname{{{run_text}}}"
        elif run_label == "TEXT":
            base = rf"\text{{{run_text}}}"
    radical = _first_child(children.get("RADICAL_BODY", []), node_by_id)
    if base in {r"\sqrt", "√", "sqrt"}:
        if radical:
            radical_index = _first_child(children.get("RADICAL_INDEX", []), node_by_id)
            index_text = ""
            if radical_index:
                index_text = _decode_node(str(radical_index.get("target", "")), node_by_id, by_source, visited, warnings)
            radical_text = _decode_node(str(radical.get("target", "")), node_by_id, by_source, visited, warnings)
            if index_text:
                base = rf"\sqrt[{index_text}]{{{radical_text}}}" if radical_text else rf"\sqrt[{index_text}]{{}}"
            else:
                base = rf"\sqrt{{{radical_text}}}" if radical_text else r"\sqrt{}"
            return base
        warnings.add("decoder_radical_missing_body_relation")
        base = r"\sqrt{}"
    sup = _first_child(children.get("SUP", []), node_by_id) or _first_child(children.get("OVER", []), node_by_id)
    sub = _first_child(children.get("SUB", []), node_by_id) or _first_child(children.get("UNDER", []), node_by_id)
    pre_sup = _first_child(children.get("PRE_SUP", []), node_by_id)
    pre_sub = _first_child(children.get("PRE_SUB", []), node_by_id)
    if pre_sub or pre_sup:
        prefix = "{}"
        if pre_sub:
            pre_sub_text = _decode_node(str(pre_sub.get("target", "")), node_by_id, by_source, visited, warnings)
            if pre_sub_text:
                prefix = f"{prefix}_{{{pre_sub_text}}}"
        if pre_sup:
            pre_sup_text = _decode_node(str(pre_sup.get("target", "")), node_by_id, by_source, visited, warnings)
            if pre_sup_text:
                prefix = f"{prefix}^{{{pre_sup_text}}}"
        base = f"{prefix}{base}"
    if sub:
        sub_text = _decode_node(str(sub.get("target", "")), node_by_id, by_source, visited, warnings)
        if sub_text:
            base = f"{base}_{{{sub_text}}}"
    if sup:
        sup_text = _decode_node(str(sup.get("target", "")), node_by_id, by_source, visited, warnings)
        if sup_text:
            base = f"{base}^{{{sup_text}}}"
    right = _first_child(children.get("HORIZONTAL", []), node_by_id)
    if right is None and len(text_run_ids) <= 1:
        right = _first_child(children.get("TEXT_RUN_NEXT", []), node_by_id)
    if right:
        right_text = _decode_node(str(right.get("target", "")), node_by_id, by_source, visited, warnings)
        if right_text:
            base = f"{base}{right_text}"
    fence_open, fence_close = _fence_delimiters_for_body(node_id, node_by_id, by_source)
    if fence_open:
        visited.add(fence_open)
        base = f"{_glyph_latex(node_by_id[fence_open])}{base}"
    if fence_close:
        visited.add(fence_close)
        base = f"{base}{_glyph_latex(node_by_id[fence_close])}"
    return base


def _text_run_chain(
    node_id: str,
    node_by_id: dict[str, dict[str, Any]],
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    visited: set[str],
    warnings: set[str],
) -> list[str]:
    run_label = _model_text_run_label(node_id, node_by_id)
    if not run_label:
        return [node_id]
    chain = [node_id]
    seen = {node_id}
    current = node_id
    while True:
        children = by_source.get(current, {})
        next_relation = _first_child(
            [
                relation
                for relation in children.get("TEXT_RUN_NEXT", [])
                if _is_forward_reading_relation(current, str(relation.get("target", "") or ""), node_by_id)
            ],
            node_by_id,
        )
        if next_relation is None:
            break
        target = str(next_relation.get("target", "") or "")
        if target in seen:
            warnings.add("decoder_text_run_cycle")
            break
        if target in visited:
            break
        if _model_text_run_label(target, node_by_id) != run_label:
            break
        chain.append(target)
        seen.add(target)
        visited.add(target)
        current = target
    return chain


def _is_model_text_node(node_id: str, node_by_id: dict[str, dict[str, Any]]) -> bool:
    return bool(_model_text_run_label(node_id, node_by_id))


def _model_text_run_label(node_id: str, node_by_id: dict[str, dict[str, Any]]) -> str:
    node = node_by_id.get(node_id, {})
    label = str(node.get("model_label", "") or node.get("label", "") or "")
    confidence = _float(node.get("model_confidence", node.get("label_confidence", 0.0)))
    if label in {"TEXT", "OPERATOR"} and confidence >= 0.65:
        return label
    return ""


def _glyph_plain_text(glyph: dict[str, Any]) -> str:
    text = str(glyph.get("unicode", "") or glyph.get("text", "") or "")
    if text:
        return text
    raw = glyph.get("raw", {}) if isinstance(glyph.get("raw", {}), dict) else {}
    text = str(raw.get("text", "") or "")
    if text:
        return text
    return _glyph_latex(glyph).replace("\\", "")


def _fence_delimiters_for_body(
    node_id: str,
    node_by_id: dict[str, dict[str, Any]],
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
) -> tuple[str, str]:
    children = by_source.get(node_id, {})
    open_relation = _first_child(children.get("FENCE_OPEN", []), node_by_id)
    close_relation = _first_child(children.get("FENCE_CLOSE", []), node_by_id)
    open_id = str(open_relation.get("target", "") or "") if open_relation else ""
    close_id = str(close_relation.get("target", "") or "") if close_relation else ""
    return open_id, close_id


def _decode_rule_structures(
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
    visited: set[str],
    warnings: set[str],
) -> list[str]:
    parts: list[str] = []
    matrix = _decode_matrix_structures(by_source, node_by_id, visited, warnings)
    if matrix:
        parts.extend(matrix)
    for source in sorted(by_source, key=lambda node_id: _glyph_sort_key(node_by_id.get(node_id, {}))):
        if source not in node_by_id:
            continue
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
            body_relations = _ordered_children(children.get("BELOW", []), node_by_id) or _ordered_children(children.get("ABOVE", []), node_by_id)
            if body_relations:
                body = _decode_group([str(item.get("target", "")) for item in body_relations], node_by_id, by_source, visited, warnings)
                parts.append(rf"\overline{{{body}}}")
            else:
                warnings.add("decoder_overline_missing_group")
        elif children.get("UNDERLINE"):
            body_relations = _ordered_children(children.get("ABOVE", []), node_by_id) or _ordered_children(children.get("BELOW", []), node_by_id)
            if body_relations:
                body = _decode_group([str(item.get("target", "")) for item in body_relations], node_by_id, by_source, visited, warnings)
                parts.append(rf"\underline{{{body}}}")
            else:
                warnings.add("decoder_underline_missing_group")
        elif children.get("ACCENT_BASE"):
            body_relations = _ordered_children(children.get("ACCENT_BASE", []), node_by_id)
            accent = _glyph_latex(node_by_id.get(source, {}))
            if body_relations and accent.startswith("\\"):
                body = _decode_group([str(item.get("target", "")) for item in body_relations], node_by_id, by_source, visited, warnings)
                visited.add(source)
                parts.append(rf"{accent}{{{body}}}")
            else:
                warnings.add("decoder_accent_missing_group")
        elif children.get("ENCLOSURE_BODY"):
            body_relations = _ordered_children(children.get("ENCLOSURE_BODY", []), node_by_id)
            if body_relations:
                body = _decode_group([str(item.get("target", "")) for item in body_relations], node_by_id, by_source, visited, warnings)
                visited.add(source)
                parts.append(body)
                warnings.add("decoder_enclosure_body_unwrapped")
            else:
                warnings.add("decoder_enclosure_missing_body")
    return parts


def _decode_matrix_structures(
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
    visited: set[str],
    warnings: set[str],
) -> list[str]:
    rows = _matrix_rows_from_relations(by_source, node_by_id)
    if not rows:
        return []
    used: set[str] = set()
    row_texts: list[str] = []
    for row in rows:
        visible_row = [node_id for node_id in row if node_id not in used and node_id in node_by_id]
        if not visible_row:
            continue
        used.update(visible_row)
        row_texts.append(" & ".join(_decode_matrix_cell(node_id, node_by_id, by_source, visited, warnings) for node_id in visible_row))
    if not row_texts:
        return []
    visited.update(used)
    return [r"\begin{matrix}" + r"\\".join(row_texts) + r"\end{matrix}"]


def _matrix_rows_from_relations(
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[list[str]]:
    row_sources = {
        source
        for source, children in by_source.items()
        if source in node_by_id and children.get("MATRIX_CELL")
    }
    row_targets = {
        str(item.get("target", "") or "")
        for children in by_source.values()
        for item in children.get("MATRIX_ROW", [])
        if str(item.get("target", "") or "") in node_by_id
    }
    row_heads = sorted(row_sources - row_targets, key=lambda node_id: _glyph_sort_key(node_by_id[node_id]))
    rows: list[list[str]] = []
    seen_rows: set[str] = set()
    for row_head in row_heads:
        current = row_head
        while current and current not in seen_rows:
            seen_rows.add(current)
            rows.append(_matrix_cell_chain(current, by_source, node_by_id))
            next_row = _first_child(by_source.get(current, {}).get("MATRIX_ROW", []), node_by_id)
            current = str(next_row.get("target", "") or "") if next_row else ""
    for row_source in sorted(row_sources - seen_rows, key=lambda node_id: _glyph_sort_key(node_by_id[node_id])):
        rows.append(_matrix_cell_chain(row_source, by_source, node_by_id))
    if rows:
        return rows
    return _legacy_matrix_rows_from_row_relations(by_source, node_by_id)


def _matrix_cell_chain(
    row_anchor: str,
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    cells = [row_anchor]
    seen = {row_anchor}
    current = row_anchor
    while current:
        next_cell = _first_child(by_source.get(current, {}).get("MATRIX_CELL", []), node_by_id)
        if next_cell is None:
            break
        target = str(next_cell.get("target", "") or "")
        if not target or target in seen:
            break
        cells.append(target)
        seen.add(target)
        current = target
    return cells


def _legacy_matrix_rows_from_row_relations(
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    node_by_id: dict[str, dict[str, Any]],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for source in sorted(by_source, key=lambda node_id: (_glyph_sort_key(node_by_id.get(node_id, {}))[1], _glyph_sort_key(node_by_id.get(node_id, {}))[0], node_id)):
        if source not in node_by_id:
            continue
        row_relations = _ordered_children(by_source[source].get("MATRIX_ROW", []), node_by_id)
        if not row_relations:
            continue
        row_ids = [source]
        row_ids.extend(str(item.get("target", "") or "") for item in row_relations)
        unique_row_ids = _unique_node_ids(row_ids)
        if len(unique_row_ids) >= 2:
            rows.append(unique_row_ids)
    return rows


def _decode_matrix_cell(
    node_id: str,
    node_by_id: dict[str, dict[str, Any]],
    by_source: dict[str, dict[str, list[dict[str, Any]]]],
    visited: set[str],
    warnings: set[str],
) -> str:
    children = by_source.get(node_id, {})
    content = [node_id]
    content.extend(str(item.get("target", "") or "") for item in _ordered_children(children.get("CELL_CONTENT", []), node_by_id))
    return _decode_group(_unique_node_ids(content), node_by_id, by_source, visited, warnings)


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
        rule_relations = (
            children.get("ABOVE", [])
            + children.get("BELOW", [])
            + children.get("ACCENT_BASE", [])
            + children.get("FENCE_OPEN", [])
            + children.get("FENCE_CLOSE", [])
            + children.get("MATRIX_ROW", [])
            + children.get("MATRIX_CELL", [])
            + children.get("CELL_CONTENT", [])
            + children.get("ENCLOSURE_BODY", [])
            + children.get("EQUATION_TAG", [])
        )
        if not (
            children.get("FRACTION_BAR")
            or children.get("OVERLINE")
            or children.get("UNDERLINE")
            or children.get("ACCENT_BASE")
            or children.get("MATRIX_ROW")
            or children.get("MATRIX_CELL")
            or children.get("CELL_CONTENT")
            or children.get("ENCLOSURE_BODY")
            or children.get("EQUATION_TAG")
        ):
            rule_relations = children.get("FENCE_OPEN", []) + children.get("FENCE_CLOSE", [])
        for relation in rule_relations:
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


def _unique_node_ids(node_ids: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for node_id in node_ids:
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        output.append(node_id)
    return output


def _is_forward_reading_relation(
    source: str,
    target: str,
    node_by_id: dict[str, dict[str, Any]],
) -> bool:
    if source not in node_by_id or target not in node_by_id:
        return False
    return _glyph_sort_key(node_by_id[source]) < _glyph_sort_key(node_by_id[target])


def _glyph_latex(glyph: dict[str, Any]) -> str:
    latex = str(glyph.get("latex", "") or "")
    if latex:
        return latex
    unicode_value = str(glyph.get("unicode", "") or "")
    if unicode_value:
        return latex_for_unicode_text(unicode_value)
    raw = glyph.get("raw", {}) if isinstance(glyph.get("raw", {}), dict) else {}
    return latex_for_unicode_text(str(raw.get("text", "") or ""))


def _decode_linear_glyphs(glyphs: list[dict[str, Any]]) -> str:
    return "".join(_glyph_latex(glyph) for glyph in sorted(glyphs, key=_glyph_sort_key))


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


def _attach_node_predictions(
    node_by_id: dict[str, dict[str, Any]],
    structural_candidate: dict[str, Any],
) -> None:
    for item in structural_candidate.get("node_predictions", []) or []:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("node_id", "") or "")
        if node_id not in node_by_id:
            continue
        node = dict(node_by_id[node_id])
        node["model_label"] = str(item.get("label", "") or "")
        node["model_confidence"] = _float(item.get("confidence"))
        node_by_id[node_id] = node


def _compact_part(text: str) -> str:
    return text.replace(" ", "")


def _fallback(fallback_text: str, warnings: set[str]) -> TinyBDDecodedLatex:
    return TinyBDDecodedLatex(
        decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
        latex=str(fallback_text or ""),
        confidence=0.0,
        warnings=tuple(sorted(warnings)),
    )


def _finalize_decoded(
    decoded: TinyBDDecodedLatex,
    *,
    glyphs: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    vectors: list[dict[str, Any]] | None,
) -> TinyBDDecodedLatex:
    verification = verify_layout_candidate(
        glyphs,
        structural_candidate,
        decoded_latex=decoded.latex,
        decoded_confidence=decoded.confidence,
        decoder_warnings=decoded.warnings,
        vectors=vectors,
    )
    warnings = tuple(sorted(set(decoded.warnings) | set(verification.warnings)))
    confidence = (
        min(decoded.confidence, verification.confidence)
        if decoded.confidence > 0
        else verification.confidence
    )
    constrained = verification.to_json().get("constrained_decode", {})
    canonical_cslt = constrained.get("canonical_cslt", {}) if isinstance(constrained, dict) else {}
    n_best_cslt = constrained.get("n_best_cslt", ()) if isinstance(constrained, dict) else ()
    latex_candidates = _latex_candidates_from_cslt(
        glyphs=glyphs,
        vectors=vectors or [],
        structural_candidate=structural_candidate,
        n_best_cslt=tuple(item for item in n_best_cslt if isinstance(item, dict)),
        selected_latex=decoded.latex,
        selected_confidence=round(max(0.0, min(1.0, confidence)), 6),
        layout_status=verification.status,
        layout_confidence=verification.confidence,
        layout_warnings=verification.warnings,
    )
    verifier_ranked_candidates = _verifier_ranked_candidates(latex_candidates)
    return TinyBDDecodedLatex(
        decoder_version=decoded.decoder_version,
        latex=decoded.latex,
        confidence=round(max(0.0, min(1.0, confidence)), 6),
        warnings=warnings,
        candidate_only=True,
        accepted=False,
        abstain=verification.status == "abstain",
        layout_status=verification.status,
        layout_confidence=verification.confidence,
        layout_warnings=verification.warnings,
        layout_verification=verification.to_json(),
        canonical_cslt=canonical_cslt,
        n_best_cslt=tuple(item for item in n_best_cslt if isinstance(item, dict)),
        latex_candidates=latex_candidates,
        verifier_ranked_candidates=verifier_ranked_candidates,
        manual_review_recommendation=_manual_review_recommendation(latex_candidates),
    )


def _latex_candidates_from_cslt(
    *,
    glyphs: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    n_best_cslt: tuple[dict[str, Any], ...],
    selected_latex: str,
    selected_confidence: float,
    layout_status: str,
    layout_confidence: float,
    layout_warnings: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    candidates: list[dict[str, Any]] = []
    by_latex: dict[str, dict[str, Any]] = {}
    if str(selected_latex or "").strip():
        selected_payload = {
            "rank": 1,
            "latex": str(selected_latex),
            "confidence": float(selected_confidence),
            "layout_status": layout_status,
            "layout_confidence": float(layout_confidence),
            "layout_warnings": list(layout_warnings),
            "selection_blockers": _candidate_selection_blockers(
                rank=1,
                layout_status=layout_status,
                warnings=layout_warnings,
            ),
            "source": "selected_structural_candidate",
            "alternative_structure_evidence": [],
            "candidate_only": True,
            "accepted": False,
        }
        candidates.append(selected_payload)
        by_latex[str(selected_latex)] = selected_payload
    for cslt in n_best_cslt:
        candidate = _latex_candidate_from_cslt_candidate(
            glyphs=glyphs,
            vectors=vectors,
            structural_candidate=structural_candidate,
            cslt=cslt,
            rank=len(candidates) + 1,
        )
        if candidate is None:
            continue
        latex = str(candidate.get("latex", "") or "")
        existing = by_latex.get(latex)
        if existing is not None:
            evidence = _alternative_structure_evidence(candidate)
            if evidence:
                existing.setdefault("alternative_structure_evidence", []).append(evidence)
            continue
        candidates.append(candidate)
        by_latex[latex] = candidate
        if len(candidates) >= 3:
            break
    ranked = _verifier_ranked_candidates(candidates)
    return tuple(_with_verifier_rank(candidates, ranked))


def _latex_candidate_from_cslt_candidate(
    *,
    glyphs: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    cslt: dict[str, Any],
    rank: int,
) -> dict[str, Any] | None:
    relations = [
        relation
        for relation in cslt.get("relations", []) or []
        if isinstance(relation, dict)
    ]
    if not relations:
        return None
    decoded = _decode_relations_without_verifier(
        glyphs=glyphs,
        vectors=vectors,
        structural_candidate=structural_candidate,
        relations=relations,
    )
    latex = str(decoded.latex or "")
    if not latex.strip():
        return None
    candidate_structural = _structural_candidate_for_relations(structural_candidate, relations)
    verification = verify_layout_candidate(
        glyphs,
        candidate_structural,
        decoded_latex=latex,
        decoded_confidence=decoded.confidence,
        decoder_warnings=decoded.warnings,
        vectors=vectors,
    )
    return {
        "rank": int(rank),
        "latex": latex,
        "confidence": min(decoded.confidence, verification.confidence)
        if decoded.confidence > 0
        else verification.confidence,
        "warnings": sorted(set(decoded.warnings) | set(verification.warnings)),
        "layout_status": verification.status,
        "layout_confidence": verification.confidence,
        "layout_warnings": list(verification.warnings),
        "layout_verification": verification.to_json(),
        "selection_blockers": _candidate_selection_blockers(
            rank=rank,
            layout_status=verification.status,
            warnings=verification.warnings,
        ),
        "source": str(cslt.get("source", "") or "n_best_cslt"),
        "cslt_candidate_id": str(cslt.get("candidate_id", "") or ""),
        "alternative_structure_evidence": [],
        "candidate_only": True,
        "accepted": False,
    }


def _alternative_structure_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    source = str(candidate.get("source", "") or "")
    cslt_candidate_id = str(candidate.get("cslt_candidate_id", "") or "")
    if cslt_candidate_id == "selected":
        return {}
    if not source and not cslt_candidate_id:
        return {}
    return {
        "rank": int(candidate.get("rank", 999) or 999),
        "source": source,
        "cslt_candidate_id": cslt_candidate_id,
        "latex": str(candidate.get("latex", "") or ""),
        "confidence": float(candidate.get("confidence", 0.0) or 0.0),
        "layout_status": str(candidate.get("layout_status", "") or ""),
        "layout_confidence": float(candidate.get("layout_confidence", 0.0) or 0.0),
        "selection_blockers": list(candidate.get("selection_blockers", []) or []),
        "warnings": list(candidate.get("warnings", []) or []),
        "candidate_only": True,
        "accepted": False,
    }


def _verifier_ranked_candidates(candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    ranked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        entry = _verifier_ranking_entry(candidate)
        key = (str(entry.get("source", "") or ""), str(entry.get("cslt_candidate_id", "") or ""))
        if key not in seen:
            ranked.append(entry)
            seen.add(key)
        for alternative in candidate.get("alternative_structure_evidence", []) or []:
            if not isinstance(alternative, dict):
                continue
            alt_entry = _verifier_ranking_entry(
                {
                    **alternative,
                    "rank": int(alternative.get("rank", candidate.get("rank", 999)) or 999),
                    "latex": candidate.get("latex", ""),
                }
            )
            alt_key = (str(alt_entry.get("source", "") or ""), str(alt_entry.get("cslt_candidate_id", "") or ""))
            if alt_key in seen:
                continue
            ranked.append(alt_entry)
            seen.add(alt_key)
    return tuple(
        sorted(
            ranked,
            key=lambda item: (
                _layout_status_rank(str(item.get("layout_status", "") or "")),
                float(item.get("layout_confidence", 0.0) or 0.0),
                float(item.get("confidence", 0.0) or 0.0),
                -int(item.get("rank", 999) or 999),
            ),
            reverse=True,
        )
    )


def _verifier_ranking_entry(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": int(candidate.get("rank", 999) or 999),
        "latex": str(candidate.get("latex", "") or ""),
        "confidence": float(candidate.get("confidence", 0.0) or 0.0),
        "layout_status": str(candidate.get("layout_status", "") or ""),
        "layout_confidence": float(candidate.get("layout_confidence", 0.0) or 0.0),
        "selection_blockers": list(candidate.get("selection_blockers", []) or []),
        "warnings": list(candidate.get("warnings", candidate.get("layout_warnings", [])) or []),
        "source": str(candidate.get("source", "") or ""),
        "cslt_candidate_id": str(candidate.get("cslt_candidate_id", "") or ""),
        "candidate_only": True,
        "accepted": False,
    }


def _with_verifier_rank(
    candidates: list[dict[str, Any]],
    ranked: tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    rank_by_key = {
        (str(item.get("source", "") or ""), str(item.get("cslt_candidate_id", "") or "")): index
        for index, item in enumerate(ranked, start=1)
    }
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        payload = dict(candidate)
        key = (str(payload.get("source", "") or ""), str(payload.get("cslt_candidate_id", "") or ""))
        verifier_rank = rank_by_key.get(key)
        if verifier_rank is not None:
            payload["verifier_rank"] = verifier_rank
        output.append(payload)
    return output


def _structural_candidate_for_relations(
    structural_candidate: dict[str, Any],
    relations: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(structural_candidate)
    payload["selected_relations"] = [dict(item) for item in relations]
    payload["relation_alternatives"] = []
    payload["abstain"] = not bool(relations)
    payload["verifier_warnings"] = [
        item
        for item in payload.get("verifier_warnings", []) or []
        if str(item) != "graph_parser_no_selected_relations" or not relations
    ]
    return payload


def _candidate_selection_blockers(
    *,
    rank: int,
    layout_status: str,
    warnings: tuple[str, ...] | list[str],
) -> list[str]:
    blockers: list[str] = []
    if rank != 1:
        blockers.append("not_rank_one_selected_candidate")
    if layout_status == "abstain":
        blockers.append("layout_abstain")
    elif layout_status == "review":
        blockers.append("layout_review_required")
    blockers.extend(str(item) for item in warnings if str(item).startswith("constraint_"))
    return sorted(set(blockers))


def _manual_review_recommendation(candidates: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not candidates:
        return {
            "candidate_only": True,
            "accepted": False,
            "auto_accept_allowed": False,
            "reason": "no_latex_candidates",
        }
    ranked = _verifier_ranked_candidates(candidates)
    best = ranked[0]
    rank = int(best.get("rank", 0) or 0)
    blockers = set(str(item) for item in best.get("selection_blockers", []) or [])
    if rank != 1:
        blockers.add("manual_review_required_for_non_rank_one_candidate")
    return {
        "candidate_only": True,
        "accepted": False,
        "auto_accept_allowed": False,
        "recommended_rank": rank,
        "latex": str(best.get("latex", "") or ""),
        "confidence": float(best.get("confidence", 0.0) or 0.0),
        "layout_status": str(best.get("layout_status", "") or ""),
        "layout_confidence": float(best.get("layout_confidence", 0.0) or 0.0),
        "selection_blockers": sorted(blockers),
        "source": str(best.get("source", "") or ""),
        "cslt_candidate_id": str(best.get("cslt_candidate_id", "") or ""),
        "reason": "highest_verifier_confidence_candidate_for_manual_review",
    }


def _layout_status_rank(status: str) -> int:
    if status == "pass":
        return 3
    if status == "review":
        return 2
    if status == "abstain":
        return 1
    return 0


def _decode_relations_without_verifier(
    *,
    glyphs: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    structural_candidate: dict[str, Any],
    relations: list[dict[str, Any]],
) -> TinyBDDecodedLatex:
    filtered_node_ids = _model_filtered_node_ids(structural_candidate)
    glyph_by_id = {
        _node_id(glyph): glyph
        for glyph in glyphs
        if _node_id(glyph) and _node_id(glyph) not in filtered_node_ids
    }
    vector_by_id = {
        _node_id(vector): vector
        for vector in vectors
        if _node_id(vector) and _node_id(vector) not in filtered_node_ids
    }
    node_by_id: dict[str, dict[str, Any]] = {**glyph_by_id, **vector_by_id}
    _attach_node_predictions(node_by_id, structural_candidate)
    warnings: set[str] = set()
    if not glyph_by_id:
        return _fallback("", {"decoder_no_glyphs"})
    if not relations:
        return TinyBDDecodedLatex(
            decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
            latex=_decode_linear_glyphs(list(glyph_by_id.values())),
            confidence=0.0,
            warnings=("decoder_no_selected_relations",),
        )
    by_source: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    has_parent: set[str] = set()
    supported_relations: list[dict[str, Any]] = []
    for relation in relations:
        source = str(relation.get("source", "") or "")
        target = str(relation.get("target", "") or "")
        kind = str(relation.get("relation", "") or "")
        if kind not in DECODER_SUPPORTED_RELATIONS:
            warnings.add("decoder_unsupported_relation_labels")
            continue
        if source not in node_by_id or target not in node_by_id:
            warnings.add("decoder_relation_references_missing_glyph")
            continue
        if kind == "TEXT_RUN_NEXT" and not _is_forward_reading_relation(source, target, node_by_id):
            warnings.add("decoder_ignored_backward_text_run_next")
            continue
        supported_relations.append(relation)
        by_source[source][kind].append(relation)
        if kind in {"HORIZONTAL", "SUP", "SUB", "PRE_SUP", "PRE_SUB", "UNDER", "OVER", "ABOVE", "BELOW", "RADICAL_BODY", "RADICAL_INDEX", "TEXT_RUN_NEXT", "FENCE_OPEN", "FENCE_CLOSE", "MATRIX_ROW", "MATRIX_CELL", "CELL_CONTENT", "ACCENT_BASE", "ENCLOSURE_BODY", "EQUATION_TAG"}:
            has_parent.add(target)
        if kind in {"FRACTION_BAR", "OVERLINE", "UNDERLINE"} and source == target:
            has_parent.add(source)
    if not supported_relations:
        return TinyBDDecodedLatex(
            decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
            latex=_decode_linear_glyphs(list(glyph_by_id.values())),
            confidence=0.0,
            warnings=tuple(sorted(warnings | {"decoder_no_supported_relations"})),
        )
    consumed_by_rule = _rule_consumed_nodes(by_source, node_by_id)
    roots = [node_id for node_id in glyph_by_id if node_id not in has_parent and node_id not in consumed_by_rule]
    parts: list[str] = []
    visited: set[str] = set()
    parts.extend(_decode_rule_structures(by_source, node_by_id, visited, warnings))
    if not roots and not parts:
        roots = list(glyph_by_id)
        warnings.add("decoder_no_root")
    for root in sorted(roots, key=lambda node_id: _glyph_sort_key(glyph_by_id[node_id])):
        text = _decode_node(root, node_by_id, by_source, visited, warnings)
        if text:
            parts.append(text)
    latex = "".join(_compact_part(part) for part in parts if part).strip()
    return TinyBDDecodedLatex(
        decoder_version=TINYBDMATH_LATEX_DECODER_VERSION,
        latex=latex,
        confidence=_decode_confidence(supported_relations, warnings),
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
