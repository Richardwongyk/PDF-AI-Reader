"""Build TinyBDMath CSLT target trees from source LaTeX for training/audit."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any
from xml.etree import ElementTree

from src.core.latex_mathml_extractor import KaTeXMathMLExtractor
from src.core.symbol_identity_repair import latex_for_unicode_text
from src.core.tinybdmath_cslt_schema import CSLTBuilder, CSLTTree


TARGET_TREE_SCHEMA_VERSION = "tinybdmath_target_tree_rows_v1"
TARGET_TREE_BUILDER_VERSION = "tinybdmath_katex_to_cslt_v1"


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
        warnings = set(str(item) for item in extracted.warnings if item)
        if extracted.warnings or not extracted.parse_tree:
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
            warnings=tuple(sorted(warnings)),
        )

    def build_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        latex_key: str = "label_latex",
        limit: int = 0,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected = rows[: max(0, int(limit))] if limit > 0 else rows
        output: list[dict[str, Any]] = []
        warnings: Counter[str] = Counter()
        structure_counts: Counter[str] = Counter()
        for row in selected:
            result = self.build_from_latex(
                str(row.get(latex_key, "") or ""),
                row_id=str(row.get("row_id", "") or ""),
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


class _KaTeXToCSLT:
    def __init__(self, *, mathml_text: str = "") -> None:
        self.builder = CSLTBuilder()
        self.warnings: set[str] = set()
        self._mathml_chars = list(str(mathml_text or ""))
        self._mathml_index = 0

    def finish(self, root_id: str, *, metadata: dict[str, Any]) -> CSLTTree:
        return self.builder.build(root_id, metadata=metadata, warnings=tuple(sorted(self.warnings)))

    def build_group(self, nodes: Any, *, group_role: str = "group") -> str:
        root = self.builder.add_node("group", attrs={"role": group_role})
        for index, child in enumerate(_as_list(nodes)):
            child_id = self.build_node(child)
            if child_id:
                self.builder.add_edge(root, child_id, "child", order=index)
        return root

    def build_node(self, node: Any) -> str:
        if not isinstance(node, dict):
            self.warnings.add("target_tree_non_object_node")
            return self.builder.add_node("artifact", attrs={"reason": "non_object_node"})
        node_type = str(node.get("type", "") or "")
        if node_type in {"mathord", "textord", "atom", "op", "bin", "rel", "open", "close", "punct"}:
            text = _symbol_text(node)
            aliases = self._consume_mathml_aliases(text)
            return self.builder.add_node(
                "symbol",
                value=text,
                latex=_symbol_latex(text),
                attrs={
                    "katex_type": node_type,
                    "mode": str(node.get("mode", "") or ""),
                    "identity_aliases": aliases,
                },
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
        if node_type == "font":
            return self._font(node)
        if node_type == "leftright":
            return self._fence(node)
        if node_type == "accent":
            return self._accent(node)
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

    def _radical(self, node: dict[str, Any]) -> str:
        radical = self.builder.add_node("radical", attrs={"katex_type": "sqrt"})
        if isinstance(node.get("body"), dict):
            self.builder.add_edge(radical, self.build_node(node.get("body")), "radical_body", order=0)
        else:
            self.warnings.add("target_tree_radical_missing_body")
        if isinstance(node.get("index"), dict):
            self.builder.add_edge(radical, self.build_node(node.get("index")), "radical_index", order=1)
        return radical

    def _fraction(self, node: dict[str, Any]) -> str:
        fraction = self.builder.add_node(
            "fraction",
            attrs={
                "katex_type": "genfrac",
                "has_bar_line": bool(node.get("hasBarLine", True)),
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

    def _matrix(self, node: dict[str, Any]) -> str:
        matrix = self.builder.add_node("matrix", attrs={"katex_type": "array", "environment": "matrix"})
        rows = _as_list(node.get("body"))
        for row_index, row in enumerate(rows):
            row_id = self.builder.add_node("group", attrs={"role": "matrix_row", "row": row_index})
            self.builder.add_edge(matrix, row_id, "matrix_row", order=row_index)
            for col_index, cell in enumerate(_as_list(row)):
                cell_id = self.builder.add_node("group", attrs={"role": "matrix_cell", "row": row_index, "column": col_index})
                self.builder.add_edge(row_id, cell_id, "matrix_cell", order=col_index)
                content_id = self.build_node(cell)
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


def _symbol_text(node: dict[str, Any]) -> str:
    for key in ("text", "name", "value"):
        value = str(node.get(key, "") or "")
        if value:
            return value
    return ""


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
