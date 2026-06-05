"""Build TinyBDMath CSLT target trees from source LaTeX for training/audit."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any
from xml.etree import ElementTree

from src.core.latex_mathml_extractor import KaTeXMathMLExtractor, LatexMathMLExtraction
from src.core.symbol_identity_repair import latex_for_unicode_text
from src.core.tinybdmath_cslt_schema import CSLTBuilder, CSLTTree


TARGET_TREE_SCHEMA_VERSION = "tinybdmath_target_tree_rows_v1"
TARGET_TREE_BUILDER_VERSION = "tinybdmath_katex_to_cslt_v3"
NONFATAL_EXTRACTION_WARNINGS = {
    "katex_display_alignment_wrapped",
    "katex_source_preprocessed",
    "katex_unbraced_control_argument_wrapped",
}


@dataclass(frozen=True)
class TinyBDTargetTreeResult:
    row_id: str
    input_hash: str
    latex: str
    display_mode: bool
    target_tree: CSLTTree | None
    parser_backend: str
    parser_version: str
    builder_version: str
    parser_summary: dict[str, Any]
    warnings: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": TARGET_TREE_SCHEMA_VERSION,
            "row_id": self.row_id,
            "input_hash": self.input_hash,
            "latex": self.latex,
            "display_mode": self.display_mode,
            "parser_backend": self.parser_backend,
            "parser_version": self.parser_version,
            "builder_version": self.builder_version,
            "parser_summary": self.parser_summary,
            "target_tree": self.target_tree.to_json() if self.target_tree is not None else None,
            "warnings": list(self.warnings),
        }


class TinyBDTargetTreeBuilder:
    """Convert KaTeX parse trees into project CSLT targets."""

    def __init__(self, *, extractor: KaTeXMathMLExtractor | None = None) -> None:
        self._extractor = extractor or KaTeXMathMLExtractor()

    def build_from_latex(
        self,
        latex: str,
        *,
        row_id: str = "",
        display_mode: bool = False,
    ) -> TinyBDTargetTreeResult:
        text = str(latex or "")
        extracted = self._extractor.extract(text, display_mode=display_mode)
        return self.build_from_extraction(
            extracted,
            row_id=row_id,
            latex=text,
            display_mode=display_mode,
        )

    def build_from_extraction(
        self,
        extracted: LatexMathMLExtraction,
        *,
        row_id: str = "",
        latex: str = "",
        display_mode: bool = False,
    ) -> TinyBDTargetTreeResult:
        text = str(latex if latex else extracted.latex or "")
        parser_summary = _parser_summary(extracted.parse_tree, extracted.mathml)
        warnings = set(str(item) for item in extracted.warnings if item)
        fatal_warnings = {item for item in warnings if item not in NONFATAL_EXTRACTION_WARNINGS}
        if fatal_warnings or not extracted.parse_tree:
            warnings.add("target_tree_parse_empty_or_failed")
            return TinyBDTargetTreeResult(
                row_id=str(row_id or ""),
                input_hash=_stable_hash(
                    {
                        "latex": text,
                        "display_mode": bool(display_mode),
                        "builder": TARGET_TREE_BUILDER_VERSION,
                    }
                ),
                latex=text,
                display_mode=bool(display_mode),
                target_tree=None,
                parser_backend=extracted.parser,
                parser_version=extracted.parser_version,
                builder_version=TARGET_TREE_BUILDER_VERSION,
                parser_summary=parser_summary,
                warnings=tuple(sorted(warnings)),
            )
        mathml_text = _mathml_presentation_text(extracted.mathml)
        builder = _KaTeXToCSLT(mathml_text=mathml_text)
        root_id = builder.build_group(extracted.parse_tree, group_role="root")
        tree = builder.finish(
            root_id,
            metadata={
                "source": "latex_source_training_audit_only",
                "source_latex_hash": _stable_hash({"latex": text}),
                "parser_backend": extracted.parser,
                "parser_version": extracted.parser_version,
                "builder_version": TARGET_TREE_BUILDER_VERSION,
                "display_mode": bool(display_mode),
            },
        )
        warnings.update(builder.warnings)
        return TinyBDTargetTreeResult(
            row_id=str(row_id or ""),
            input_hash=_stable_hash(
                {
                    "latex": text,
                    "display_mode": bool(display_mode),
                    "builder": TARGET_TREE_BUILDER_VERSION,
                    "target_tree_hash": tree.stable_hash(),
                }
            ),
            latex=text,
            display_mode=bool(display_mode),
            target_tree=tree,
            parser_backend=extracted.parser,
            parser_version=extracted.parser_version,
            builder_version=TARGET_TREE_BUILDER_VERSION,
            parser_summary=parser_summary,
            warnings=tuple(sorted(warnings)),
        )

    def build_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        latex_key: str = "label_latex",
        limit: int = 0,
        batch_size: int = 512,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected = rows[: max(0, int(limit))] if limit > 0 else rows
        output: list[dict[str, Any]] = []
        warnings: Counter[str] = Counter()
        structure_counts: Counter[str] = Counter()
        for chunk in _chunks(selected, max(1, int(batch_size or 512))):
            output_chunk, chunk_warnings, chunk_structures = self.build_row_chunk(chunk, latex_key=latex_key)
            output.extend(output_chunk)
            warnings.update(chunk_warnings)
            structure_counts.update(chunk_structures)
        manifest = {
            "schema_version": "tinybdmath_target_tree_manifest_v1",
            "builder_version": TARGET_TREE_BUILDER_VERSION,
            "rows": len(output),
            "success_rows": sum(1 for item in output if item.get("target_tree")),
            "failed_rows": sum(1 for item in output if not item.get("target_tree")),
            "latex_key": latex_key,
            "warnings": dict(sorted(warnings.items())),
            "structure_counts": dict(sorted(structure_counts.items())),
            "notes": [
                "Target CSLT rows are for training/audit only.",
                "Production born-digital parsing must not read source LaTeX.",
            ],
        }
        return output, manifest

    def build_row_chunk(
        self,
        rows: list[dict[str, Any]],
        *,
        latex_key: str = "label_latex",
    ) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
        batch = [
            {
                "latex": str(row.get(latex_key, "") or ""),
                "display_mode": str(row.get("kind", "") or "") == "display",
            }
            for row in rows
        ]
        extracted_rows = self._extractor.extract_batch(batch)
        output: list[dict[str, Any]] = []
        warnings: Counter[str] = Counter()
        structure_counts: Counter[str] = Counter()
        for row, extracted in zip(rows, extracted_rows):
            result = self.build_from_extraction(
                extracted,
                row_id=str(row.get("row_id", "") or ""),
                latex=str(row.get(latex_key, "") or ""),
                display_mode=str(row.get("kind", "") or "") == "display",
            )
            payload = result.to_json()
            payload["source_row"] = {
                "case": row.get("case", ""),
                "kind": row.get("kind", ""),
                "page_num": row.get("page_num"),
                "graph_input_hash": row.get("input_hash", ""),
                "latex_key": latex_key,
            }
            output.append(payload)
            warnings.update(str(item) for item in result.warnings if item)
            if result.target_tree is not None:
                structure_counts.update(node.node_type for node in result.target_tree.nodes)
        return output, warnings, structure_counts


class _KaTeXToCSLT:
    def __init__(self, *, mathml_text: str = "") -> None:
        self.builder = CSLTBuilder()
        self.warnings: set[str] = set()
        self._mathml_chars = list(str(mathml_text or ""))
        self._mathml_index = 0

    def finish(self, root_id: str, *, metadata: dict[str, Any]) -> CSLTTree:
        return self.builder.build(root_id, metadata=metadata, warnings=tuple(sorted(self.warnings)))

    def build_group(self, nodes: Any, *, group_role: str = "group") -> str:
        node_list = _as_list(nodes)
        if any(_is_line_break_node(child) for child in node_list):
            return self._line_group(node_list, group_role=group_role)
        root = self.builder.add_node("group", attrs={"role": group_role})
        output_index = 0
        index = 0
        while index < len(node_list):
            child = node_list[index]
            if _is_left_attachment_node(child) and index + 1 < len(node_list):
                child_id = self._left_attachment(child, node_list[index + 1])
                self.builder.add_edge(root, child_id, "child", order=output_index)
                output_index += 1
                index += 2
                continue
            child_id = self.build_node(child)
            if child_id:
                self.builder.add_edge(root, child_id, "child", order=output_index)
                output_index += 1
            index += 1
        return root

    def build_node(self, node: Any) -> str:
        if not isinstance(node, dict):
            self.warnings.add("target_tree_non_object_node")
            return self.builder.add_node("artifact", attrs={"reason": "non_object_node"})
        node_type = str(node.get("type", "") or "")
        if node_type in {"mathord", "textord", "atom", "op", "bin", "rel", "open", "close", "punct"}:
            if node_type == "op" and not _symbol_text(node) and isinstance(node.get("body"), list):
                return self._operator_body(node)
            text = _symbol_text(node)
            aliases = self._consume_mathml_aliases(text)
            attrs = {
                "katex_type": node_type,
                "mode": str(node.get("mode", "") or ""),
                "identity_aliases": aliases,
            }
            family = str(node.get("family", "") or "")
            if family:
                attrs["family"] = family
            if "limits" in node:
                attrs["limits"] = bool(node.get("limits"))
            if "alwaysHandleSupSub" in node:
                attrs["always_handle_supsub"] = bool(node.get("alwaysHandleSupSub"))
            return self.builder.add_node(
                "symbol",
                value=text,
                latex=_symbol_latex(text),
                attrs=attrs,
            )
        if node_type == "supsub":
            return self._script(node)
        if node_type == "ordgroup":
            return self.build_group(node.get("body", []), group_role="ordgroup")
        if node_type == "sqrt":
            return self._radical(node)
        if node_type == "genfrac":
            return self._fraction(node)
        if node_type == "text":
            return self._text_run(node)
        if node_type == "array":
            return self._matrix(node)
        if node_type == "styling":
            return self.build_group(node.get("body", []), group_role=f"styling:{node.get('style', '')}")
        if node_type == "color":
            return self._color(node)
        if node_type == "font":
            return self._font(node)
        if node_type == "leftright":
            return self._fence(node)
        if node_type == "accent":
            return self._accent(node)
        if node_type in {"overline", "underline"}:
            return self._line_annotation(node)
        if node_type == "horizBrace":
            return self._horiz_brace(node)
        if node_type == "xArrow":
            return self._extensible_arrow(node)
        if node_type == "operatorname":
            return self._operator_name(node)
        if node_type == "mclass":
            return self._mclass(node)
        if node_type == "enclose":
            return self._enclosure(node)
        if node_type == "middle":
            return self._middle_delimiter(node)
        if node_type == "delimsizing":
            return self._sized_delimiter(node)
        if node_type in {"phantom", "hphantom", "vphantom", "smash", "lap"}:
            return self._layout_artifact_group(node)
        if node_type == "spacing":
            return self.builder.add_node("artifact", attrs={"reason": "spacing", "katex_type": node_type})
        if node_type == "kern":
            return self.builder.add_node("artifact", attrs={"reason": "kern", "katex_type": node_type})
        self.warnings.add(f"target_tree_unsupported_katex_type:{node_type or 'missing'}")
        if isinstance(node.get("body"), list):
            return self.build_group(node.get("body", []), group_role=f"unsupported:{node_type}")
        text = _symbol_text(node)
        if text:
            aliases = self._consume_mathml_aliases(text)
            return self.builder.add_node(
                "symbol",
                value=text,
                latex=_symbol_latex(text),
                attrs={"katex_type": node_type, "identity_aliases": aliases},
            )
        return self.builder.add_node("artifact", attrs={"reason": "unsupported_katex_node", "katex_type": node_type})

    def _script(self, node: dict[str, Any]) -> str:
        if _is_limits_script(node):
            return self._under_over(node)
        script = self.builder.add_node("script", attrs={"katex_type": "supsub"})
        base = node.get("base")
        if isinstance(base, dict):
            self.builder.add_edge(script, self.build_node(base), "base", order=0)
        else:
            self.warnings.add("target_tree_script_missing_base")
        if isinstance(node.get("sub"), dict):
            self.builder.add_edge(script, self.build_node(node.get("sub")), "sub", order=1)
        if isinstance(node.get("sup"), dict):
            self.builder.add_edge(script, self.build_node(node.get("sup")), "sup", order=2)
        return script

    def _left_attachment(self, node: dict[str, Any], base_node: Any) -> str:
        script = self.builder.add_node("script", attrs={"katex_type": "supsub", "placement": "left_attachment"})
        if isinstance(node.get("sup"), dict):
            self.builder.add_edge(script, self.build_node(node.get("sup")), "pre_sup", order=0)
        if isinstance(node.get("sub"), dict):
            self.builder.add_edge(script, self.build_node(node.get("sub")), "pre_sub", order=1)
        base_id = self.build_node(base_node)
        if base_id:
            self.builder.add_edge(script, base_id, "base", order=2)
        else:
            self.warnings.add("target_tree_left_attachment_missing_base")
        return script

    def _under_over(self, node: dict[str, Any]) -> str:
        base = node.get("base")
        attrs: dict[str, Any] = {"katex_type": "supsub", "placement": "limits"}
        if isinstance(base, dict):
            attrs["base_katex_type"] = str(base.get("type", "") or "")
            if "limits" in base:
                attrs["limits"] = bool(base.get("limits"))
            if "alwaysHandleSupSub" in base:
                attrs["always_handle_supsub"] = bool(base.get("alwaysHandleSupSub"))
        stacked = self.builder.add_node("under_over", attrs=attrs)
        if isinstance(base, dict):
            self.builder.add_edge(stacked, self.build_node(base), "base", order=0)
        else:
            self.warnings.add("target_tree_under_over_missing_base")
        if isinstance(node.get("sub"), dict):
            self.builder.add_edge(stacked, self.build_node(node.get("sub")), "under", order=1)
        if isinstance(node.get("sup"), dict):
            self.builder.add_edge(stacked, self.build_node(node.get("sup")), "over", order=2)
        return stacked

    def _radical(self, node: dict[str, Any]) -> str:
        radical = self.builder.add_node("radical", attrs={"katex_type": "sqrt"})
        has_index = isinstance(node.get("index"), dict)
        if has_index:
            self.builder.add_edge(radical, self.build_node(node.get("index")), "radical_index", order=0)
        if isinstance(node.get("body"), dict):
            self.builder.add_edge(radical, self.build_node(node.get("body")), "radical_body", order=1 if has_index else 0)
        else:
            self.warnings.add("target_tree_radical_missing_body")
        return radical

    def _fraction(self, node: dict[str, Any]) -> str:
        fraction = self.builder.add_node(
            "fraction",
            attrs={
                "katex_type": "genfrac",
                "has_bar_line": bool(node.get("hasBarLine", True)),
                **_genfrac_attrs(node),
            },
        )
        if isinstance(node.get("numer"), dict):
            self.builder.add_edge(fraction, self.build_node(node.get("numer")), "numerator", order=0)
        else:
            self.warnings.add("target_tree_fraction_missing_numerator")
        if isinstance(node.get("denom"), dict):
            self.builder.add_edge(fraction, self.build_node(node.get("denom")), "denominator", order=1)
        else:
            self.warnings.add("target_tree_fraction_missing_denominator")
        return fraction

    def _text_run(self, node: dict[str, Any]) -> str:
        chars = [_symbol_text(item) for item in _as_list(node.get("body")) if isinstance(item, dict)]
        value = "".join(chars)
        self._consume_mathml_aliases(value)
        return self.builder.add_node(
            "text_run",
            value=value,
            attrs={"katex_type": "text", "mode": str(node.get("mode", "") or "")},
        )

    def _font(self, node: dict[str, Any]) -> str:
        font = str(node.get("font", "") or "")
        body = node.get("body")
        if isinstance(body, dict):
            child = self.build_node(body)
            group = self.builder.add_node("group", attrs={"role": f"font:{font}", "font": font, "katex_type": "font"})
            self.builder.add_edge(group, child, "child", order=0)
            return group
        if isinstance(body, list):
            return self.build_group(body, group_role=f"font:{font}")
        self.warnings.add("target_tree_font_missing_body")
        return self.builder.add_node("artifact", attrs={"reason": "font_missing_body", "katex_type": "font", "font": font})

    def _color(self, node: dict[str, Any]) -> str:
        color = str(node.get("color", "") or "")
        if color.lower() == "#cc0000":
            self.warnings.add("target_tree_katex_error_color_node")
            return self.builder.add_node("artifact", attrs={"reason": "katex_error_color", "katex_type": "color"})
        group = self.builder.add_node("group", attrs={"role": "color", "katex_type": "color", "color": color})
        body = node.get("body")
        if isinstance(body, list):
            for index, child in enumerate(body):
                self.builder.add_edge(group, self.build_node(child), "child", order=index)
        elif isinstance(body, dict):
            self.builder.add_edge(group, self.build_node(body), "child", order=0)
        else:
            self.warnings.add("target_tree_color_missing_body")
        return group

    def _operator_name(self, node: dict[str, Any]) -> str:
        value = _node_text_content(node.get("body"))
        self._consume_mathml_aliases(value)
        attrs: dict[str, Any] = {
            "katex_type": "operatorname",
            "mode": str(node.get("mode", "") or ""),
            "operator": True,
        }
        if "limits" in node:
            attrs["limits"] = bool(node.get("limits"))
        if "alwaysHandleSupSub" in node:
            attrs["always_handle_supsub"] = bool(node.get("alwaysHandleSupSub"))
        if not value:
            self.warnings.add("target_tree_operatorname_empty_body")
        return self.builder.add_node("text_run", value=value, attrs=attrs)

    def _operator_body(self, node: dict[str, Any]) -> str:
        group = self.builder.add_node(
            "group",
            attrs={
                "role": "operator_body",
                "katex_type": "op",
                "limits": bool(node.get("limits")),
                "always_handle_supsub": bool(node.get("alwaysHandleSupSub")),
            },
        )
        for index, child in enumerate(_as_list(node.get("body"))):
            self.builder.add_edge(group, self.build_node(child), "child", order=index)
        return group

    def _mclass(self, node: dict[str, Any]) -> str:
        atom_class = str(node.get("mclass", "") or "")
        attrs: dict[str, Any] = {"role": f"mclass:{atom_class}", "katex_type": "mclass"}
        if atom_class:
            attrs["atom_class"] = atom_class
        if "isCharacterBox" in node:
            attrs["is_character_box"] = bool(node.get("isCharacterBox"))
        group = self.builder.add_node("group", attrs=attrs)
        body = node.get("body")
        if isinstance(body, list):
            for index, child in enumerate(body):
                self.builder.add_edge(group, self.build_node(child), "child", order=index)
        elif isinstance(body, dict):
            self.builder.add_edge(group, self.build_node(body), "child", order=0)
        else:
            self.warnings.add("target_tree_mclass_missing_body")
        return group

    def _matrix(self, node: dict[str, Any]) -> str:
        attrs = _array_attrs(node)
        matrix = self.builder.add_node("matrix", attrs=attrs)
        rows = _as_list(node.get("body"))
        for row_index, row in enumerate(rows):
            row_id = self.builder.add_node("group", attrs={"role": "matrix_row", "row": row_index})
            self.builder.add_edge(matrix, row_id, "matrix_row", order=row_index)
            for col_index, cell in enumerate(_as_list(row)):
                cell_id = self.builder.add_node("group", attrs={"role": "matrix_cell", "row": row_index, "column": col_index})
                self.builder.add_edge(row_id, cell_id, "matrix_cell", order=col_index)
                content_id = self.build_node(cell)
                self.builder.add_edge(cell_id, content_id, "cell_content", order=0)
        for tag_index, tag in enumerate(_array_tags(node)):
            tag_id = self._equation_tag(tag, tag_index=tag_index)
            if tag_id:
                self.builder.add_edge(matrix, tag_id, "child", order=len(rows) + tag_index)
        return matrix

    def _equation_tag(self, tag: Any, *, tag_index: int) -> str:
        value = _node_text_content(tag)
        if not value:
            return ""
        return self.builder.add_node(
            "equation_number",
            value=value,
            attrs={"katex_type": "tag", "tag_index": int(tag_index)},
        )

    def _line_group(self, nodes: list[Any], *, group_role: str) -> str:
        rows = _split_rows_on_line_break(nodes)
        matrix = self.builder.add_node(
            "matrix",
            attrs={
                "katex_type": "cr_sequence",
                "environment": "aligned",
                "display_container": "aligned_display",
                "group_role": group_role,
                "row_count": len(rows),
            },
        )
        for row_index, row_nodes in enumerate(rows):
            row_id = self.builder.add_node("group", attrs={"role": "matrix_row", "row": row_index})
            cell_id = self.builder.add_node(
                "group",
                attrs={"role": "matrix_cell", "row": row_index, "column": 0},
            )
            content_id = self.build_group(row_nodes, group_role="aligned_row")
            self.builder.add_edge(matrix, row_id, "matrix_row", order=row_index)
            self.builder.add_edge(row_id, cell_id, "matrix_cell", order=0)
            self.builder.add_edge(cell_id, content_id, "cell_content", order=0)
        return matrix

    def _fence(self, node: dict[str, Any]) -> str:
        fence = self.builder.add_node("fence", attrs={"katex_type": "leftright"})
        left = str(node.get("left", "") or "")
        right = str(node.get("right", "") or "")
        body = node.get("body", [])
        if left:
            self.builder.add_edge(
                fence,
                self.builder.add_node(
                    "symbol",
                    value=left,
                    latex=_symbol_latex(left),
                    attrs={"identity_aliases": self._consume_mathml_aliases(left)},
                ),
                "fence_open",
                order=0,
            )
        self.builder.add_edge(fence, self.build_group(body, group_role="fence_body"), "fence_body", order=1)
        if right:
            self.builder.add_edge(
                fence,
                self.builder.add_node(
                    "symbol",
                    value=right,
                    latex=_symbol_latex(right),
                    attrs={"identity_aliases": self._consume_mathml_aliases(right)},
                ),
                "fence_close",
                order=2,
            )
        return fence

    def _accent(self, node: dict[str, Any]) -> str:
        label = str(node.get("label", "") or r"\hat")
        accent = self.builder.add_node("accent", value=label, latex=label, attrs={"katex_type": "accent"})
        if isinstance(node.get("base"), dict):
            self.builder.add_edge(accent, self.build_node(node.get("base")), "accent_base", order=0)
        else:
            self.warnings.add("target_tree_accent_missing_base")
        return accent

    def _line_annotation(self, node: dict[str, Any]) -> str:
        node_type = str(node.get("type", "") or "")
        label = "\\" + node_type
        position = "under" if node_type == "underline" else "over"
        accent = self.builder.add_node(
            "accent",
            value=label,
            latex=label,
            attrs={"katex_type": node_type, "annotation_position": position},
        )
        body = node.get("body")
        if isinstance(body, dict):
            self.builder.add_edge(accent, self.build_node(body), "accent_base", order=0)
        elif isinstance(body, list):
            self.builder.add_edge(accent, self.build_group(body, group_role=f"{node_type}_body"), "accent_base", order=0)
        else:
            self.warnings.add(f"target_tree_{node_type}_missing_body")
        return accent

    def _horiz_brace(self, node: dict[str, Any]) -> str:
        label = str(node.get("label", "") or (r"\overbrace" if node.get("isOver") else r"\underbrace"))
        position = "over" if bool(node.get("isOver")) else "under"
        accent = self.builder.add_node(
            "accent",
            value=label,
            latex=label,
            attrs={"katex_type": "horizBrace", "annotation_position": position},
        )
        base = node.get("base")
        if isinstance(base, dict):
            self.builder.add_edge(accent, self.build_node(base), "accent_base", order=0)
        else:
            self.warnings.add("target_tree_horiz_brace_missing_base")
        return accent

    def _extensible_arrow(self, node: dict[str, Any]) -> str:
        label = str(node.get("label", "") or "")
        stacked = self.builder.add_node(
            "under_over",
            attrs={"katex_type": "xArrow", "label": label, "placement": "arrow_annotation"},
        )
        base = self.builder.add_node(
            "symbol",
            value=label,
            latex=label,
            attrs={"katex_type": "xArrow", "operator": True, "identity_aliases": self._consume_mathml_aliases(label)},
        )
        self.builder.add_edge(stacked, base, "base", order=0)
        body = node.get("body")
        below = node.get("below")
        if isinstance(body, dict):
            self.builder.add_edge(stacked, self.build_node(body), "over", order=1)
        if isinstance(below, dict):
            self.builder.add_edge(stacked, self.build_node(below), "under", order=2)
        return stacked

    def _enclosure(self, node: dict[str, Any]) -> str:
        label = str(node.get("label", "") or "")
        group = self.builder.add_node(
            "group",
            attrs={"role": "enclosure", "katex_type": "enclose", "label": label},
        )
        marker = self.builder.add_node(
            "artifact",
            attrs={"reason": "enclosure", "katex_type": "enclose", "label": label},
        )
        self.builder.add_edge(group, marker, "child", order=0)
        body = node.get("body")
        if isinstance(body, dict):
            self.builder.add_edge(group, self.build_node(body), "child", order=1)
        elif isinstance(body, list):
            self.builder.add_edge(group, self.build_group(body, group_role="enclosure_body"), "child", order=1)
        else:
            self.warnings.add("target_tree_enclosure_missing_body")
        return group

    def _middle_delimiter(self, node: dict[str, Any]) -> str:
        text = _symbol_text(node)
        aliases = self._consume_mathml_aliases(text)
        return self.builder.add_node(
            "symbol",
            value=text,
            latex=_symbol_latex(text),
            attrs={"katex_type": "middle", "delimiter_role": "middle", "identity_aliases": aliases},
        )

    def _sized_delimiter(self, node: dict[str, Any]) -> str:
        text = _symbol_text(node)
        attrs = {
            "katex_type": "delimsizing",
            "delimiter_role": "sized",
            "size": node.get("size"),
            "atom_class": str(node.get("mclass", "") or ""),
            "identity_aliases": self._consume_mathml_aliases(text),
        }
        return self.builder.add_node("symbol", value=text, latex=_symbol_latex(text), attrs=attrs)

    def _layout_artifact_group(self, node: dict[str, Any]) -> str:
        node_type = str(node.get("type", "") or "")
        attrs: dict[str, Any] = {"role": f"layout:{node_type}", "katex_type": node_type}
        if "alignment" in node:
            attrs["alignment"] = str(node.get("alignment", "") or "")
        group = self.builder.add_node("group", attrs=attrs)
        marker = self.builder.add_node("artifact", attrs={"reason": node_type, "katex_type": node_type})
        self.builder.add_edge(group, marker, "child", order=0)
        body = node.get("body")
        if isinstance(body, dict):
            self.builder.add_edge(group, self.build_node(body), "child", order=1)
        elif isinstance(body, list):
            self.builder.add_edge(group, self.build_group(body, group_role=f"{node_type}_body"), "child", order=1)
        return group

    def _consume_mathml_aliases(self, source_text: str) -> list[str]:
        if not self._mathml_chars:
            return []
        text = str(source_text or "")
        if not text:
            return []
        expected = 1 if text.startswith("\\") else len(text)
        if expected <= 0 or self._mathml_index >= len(self._mathml_chars):
            return []
        start = self._mathml_index
        end = min(len(self._mathml_chars), start + expected)
        self._mathml_index = end
        alias = "".join(self._mathml_chars[start:end])
        return [alias] if alias and alias != text else []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    chunk_size = max(1, int(size or 1))
    return [rows[index : index + chunk_size] for index in range(0, len(rows), chunk_size)]


def _is_line_break_node(value: Any) -> bool:
    return isinstance(value, dict) and str(value.get("type", "") or "") == "cr"


def _is_left_attachment_node(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("type", "") or "") != "supsub":
        return False
    base = value.get("base")
    if not isinstance(base, dict) or str(base.get("type", "") or "") != "ordgroup":
        return False
    if _as_list(base.get("body")):
        return False
    return isinstance(value.get("sub"), dict) or isinstance(value.get("sup"), dict)


def _split_rows_on_line_break(nodes: list[Any]) -> list[list[Any]]:
    rows: list[list[Any]] = [[]]
    for node in nodes:
        if _is_line_break_node(node):
            rows.append([])
            continue
        rows[-1].append(node)
    return [row for row in rows if row]


def _is_limits_script(node: dict[str, Any]) -> bool:
    base = node.get("base")
    if not isinstance(base, dict):
        return False
    if str(base.get("type", "") or "") not in {"op", "operatorname"}:
        return False
    if not (isinstance(node.get("sub"), dict) or isinstance(node.get("sup"), dict)):
        return False
    return bool(base.get("limits") or base.get("alwaysHandleSupSub"))


def _genfrac_attrs(node: dict[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for key, output_key in (
        ("leftDelim", "left_delimiter"),
        ("rightDelim", "right_delimiter"),
        ("size", "size"),
        ("continued", "continued"),
    ):
        if key in node:
            attrs[output_key] = node.get(key)
    bar_size = node.get("barSize")
    if isinstance(bar_size, dict):
        attrs["bar_size"] = dict(bar_size)
    elif bar_size is not None:
        attrs["bar_size"] = bar_size
    return attrs


def _array_attrs(node: dict[str, Any]) -> dict[str, Any]:
    col_separation = str(node.get("colSeparationType", "") or "")
    is_aligned = col_separation == "align"
    rows = _as_list(node.get("body"))
    column_count = max((len(_as_list(row)) for row in rows), default=0)
    attrs: dict[str, Any] = {
        "katex_type": "array",
        "environment": "aligned" if is_aligned else "matrix",
        "row_count": len(rows),
        "column_count": column_count,
    }
    if col_separation:
        attrs["col_separation"] = col_separation
    if "arraystretch" in node:
        attrs["arraystretch"] = node.get("arraystretch")
    if is_aligned:
        attrs["display_container"] = "aligned_display"
    return attrs


def _array_tags(node: dict[str, Any]) -> list[Any]:
    tags = node.get("tags")
    if not isinstance(tags, list):
        return []
    output: list[Any] = []
    for row_tags in tags:
        output.extend(_as_list(row_tags))
    return output


def _symbol_text(node: dict[str, Any]) -> str:
    for key in ("text", "name", "value", "delim"):
        value = str(node.get(key, "") or "")
        if value:
            return value
    return ""


def _node_text_content(value: Any) -> str:
    if isinstance(value, list):
        return "".join(_node_text_content(item) for item in value)
    if not isinstance(value, dict):
        return ""
    direct = _symbol_text(value)
    if direct:
        return direct
    chunks: list[str] = []
    for key in ("body", "base", "sub", "sup", "numer", "denom", "index", "tags"):
        chunks.append(_node_text_content(value.get(key)))
    return "".join(chunks)


def _symbol_latex(text: str) -> str:
    value = str(text or "")
    if value.startswith("\\"):
        return value
    return latex_for_unicode_text(value)


def _mathml_presentation_text(mathml: str) -> str:
    value = str(mathml or "").strip()
    if not value:
        return ""
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError:
        return ""
    return "".join(_iter_presentation_text(root)).strip()


def _parser_summary(parse_tree: Any, mathml: str) -> dict[str, Any]:
    katex_types: Counter[str] = Counter()
    katex_families: Counter[str] = Counter()
    mathml_tags: Counter[str] = Counter()
    _collect_katex_summary(parse_tree, katex_types, katex_families)
    value = str(mathml or "").strip()
    if value:
        try:
            root = ElementTree.fromstring(value)
        except ElementTree.ParseError:
            root = None
        if root is not None:
            for node in root.iter():
                tag = _local_name(node.tag)
                if tag:
                    mathml_tags[tag] += 1
    return {
        "katex_type_counts": dict(sorted(katex_types.items())),
        "katex_family_counts": dict(sorted(katex_families.items())),
        "mathml_tag_counts": dict(sorted(mathml_tags.items())),
    }


def _collect_katex_summary(value: Any, types: Counter[str], families: Counter[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_katex_summary(item, types, families)
        return
    if not isinstance(value, dict):
        return
    node_type = str(value.get("type", "") or "")
    if node_type:
        types[node_type] += 1
    family = str(value.get("family", "") or "")
    if family:
        families[family] += 1
    for key in ("body", "base", "sub", "sup", "numer", "denom", "index", "above", "below", "tags"):
        _collect_katex_summary(value.get(key), types, families)


def _iter_presentation_text(node: ElementTree.Element) -> list[str]:
    if _local_name(node.tag) == "annotation":
        return []
    chunks: list[str] = []
    if node.text:
        chunks.append(node.text)
    for child in list(node):
        chunks.extend(_iter_presentation_text(child))
        if child.tail:
            chunks.append(child.tail)
    return chunks


def _local_name(tag: str) -> str:
    text = str(tag or "")
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text


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
    if hasattr(value, "to_json"):
        return value.to_json()
    if hasattr(value, "__dict__"):
        return asdict(value)
    return str(value)
