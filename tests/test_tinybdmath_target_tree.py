from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder


def test_target_tree_builder_handles_core_formula_structures() -> None:
    builder = TinyBDTargetTreeBuilder()
    cases = {
        "h_{t-1}": "script",
        r"\sqrt{d_k}": "radical",
        r"d_{\text{model}}": "text_run",
        r"\frac{x}{y}": "fraction",
        r"\begin{matrix}a&b\\c&d\end{matrix}": "matrix",
    }

    for latex, expected_node_type in cases.items():
        result = builder.build_from_latex(latex, row_id=latex)
        assert result.target_tree is not None, (latex, result.warnings)
        assert any(node.node_type == expected_node_type for node in result.target_tree.nodes)
        assert result.target_tree.stable_hash()
        assert "Production born-digital parsing must not read source LaTeX." not in result.target_tree.to_latex()


def test_target_tree_build_rows_records_failures_without_dropping_rows() -> None:
    rows, manifest = TinyBDTargetTreeBuilder().build_rows(
        [
            {"row_id": "ok", "kind": "inline", "label_latex": "x_i", "input_hash": "g1"},
            {"row_id": "empty", "kind": "inline", "label_latex": "", "input_hash": "g2"},
        ]
    )

    assert len(rows) == 2
    assert manifest["rows"] == 2
    assert manifest["success_rows"] >= 1
    assert "Target CSLT rows are for training/audit only." in manifest["notes"]


def test_target_tree_builder_preserves_font_body_as_semantic_nodes() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\mathsf{Grp}", row_id="font")

    assert result.target_tree is not None
    assert [node.value for node in result.target_tree.nodes if node.node_type == "symbol"] == ["G", "r", "p"]
    assert not any(node.node_type == "artifact" for node in result.target_tree.nodes)


def test_target_tree_stores_parser_derived_identity_aliases() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\subseteq", row_id="identity")

    assert result.target_tree is not None
    symbols = [node for node in result.target_tree.nodes if node.node_type == "symbol"]
    assert symbols[0].latex == r"\subseteq"
    assert symbols[0].attrs["identity_aliases"] == ["⊆"]


def test_target_tree_records_parser_summary_for_audit() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt{d_k}", row_id="summary")
    payload = result.to_json()

    assert payload["parser_summary"]["katex_type_counts"]["sqrt"] == 1
    assert payload["parser_summary"]["katex_type_counts"]["supsub"] == 1
    assert payload["parser_summary"]["mathml_tag_counts"]["math"] == 1


def test_target_tree_represents_radical_index() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt[3]{x}", row_id="radical-index")

    assert result.target_tree is not None, result.warnings
    relations = {edge.relation for edge in result.target_tree.edges}
    assert {"radical_body", "radical_index"}.issubset(relations)
    assert result.target_tree.to_latex() == r"\sqrt[3]{x}"


def test_target_tree_represents_parser_line_breaks_as_aligned_display() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"a=b\\c=d", row_id="lines", display_mode=True)

    assert result.target_tree is not None, result.warnings
    assert "target_tree_unsupported_katex_type:cr" not in result.warnings
    matrices = [node for node in result.target_tree.nodes if node.node_type == "matrix"]
    assert matrices
    assert matrices[0].attrs["display_container"] == "aligned_display"
    assert matrices[0].attrs["row_count"] == 2


def test_target_tree_marks_katex_aligned_arrays_as_aligned_display() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(
        r"\begin{aligned}a&=b\\c&=d\end{aligned}",
        row_id="aligned",
        display_mode=True,
    )

    assert result.target_tree is not None, result.warnings
    matrices = [node for node in result.target_tree.nodes if node.node_type == "matrix"]
    assert matrices
    assert matrices[0].attrs["display_container"] == "aligned_display"


def test_target_tree_keeps_plain_matrix_separate_from_aligned_display() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(
        r"\begin{matrix}a&b\\c&d\end{matrix}",
        row_id="matrix",
        display_mode=True,
    )

    assert result.target_tree is not None, result.warnings
    matrices = [node for node in result.target_tree.nodes if node.node_type == "matrix"]
    assert matrices
    assert matrices[0].attrs["environment"] == "matrix"
    assert "display_container" not in matrices[0].attrs


def test_target_tree_represents_large_operator_limits_as_under_over() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\sum_{i=1}^{n} x_i", row_id="limits", display_mode=True)

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    assert any(node.node_type == "under_over" for node in result.target_tree.nodes)
    relations = {edge.relation for edge in result.target_tree.edges}
    assert {"base", "under", "over"}.issubset(relations)


def test_target_tree_represents_empty_base_scripts_as_left_attachment() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"{}^a_b X", row_id="left-script", display_mode=True)

    assert result.target_tree is not None, result.warnings
    scripts = [node for node in result.target_tree.nodes if node.node_type == "script"]
    assert scripts
    assert scripts[0].attrs["placement"] == "left_attachment"
    relations = {edge.relation for edge in result.target_tree.edges}
    assert {"base", "pre_sub", "pre_sup"}.issubset(relations)
    assert result.target_tree.to_latex() == r"{}_{b}^{a}X"


def test_target_tree_represents_operatorname_star_limits_as_under_over() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(
        r"\operatorname*{foo}_{x} g(x)",
        row_id="operator-limits",
        display_mode=True,
    )

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    assert any(node.node_type == "under_over" for node in result.target_tree.nodes)
    assert any(
        node.node_type == "text_run" and node.value == "foo" and node.attrs.get("operator")
        for node in result.target_tree.nodes
    )


def test_target_tree_represents_operatorname_as_operator_text_run() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\operatorname{foo}(x)", row_id="operator")

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    operators = [
        node
        for node in result.target_tree.nodes
        if node.node_type == "text_run" and node.attrs.get("katex_type") == "operatorname"
    ]
    assert operators
    assert operators[0].value == "foo"
    assert operators[0].attrs["operator"] is True


def test_target_tree_represents_line_annotations_as_accents() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\hat{x}+\overline{y}+\underline{z}", row_id="annotation")

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    accents = [node for node in result.target_tree.nodes if node.node_type == "accent"]
    assert len(accents) == 3
    assert {node.attrs["katex_type"] for node in accents} >= {"accent", "overline", "underline"}


def test_target_tree_preserves_enclosure_evidence_without_hiding_body() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\boxed{x}+\fbox{y}", row_id="enclosure")

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    assert any(node.node_type == "artifact" and node.attrs.get("reason") == "enclosure" for node in result.target_tree.nodes)
    assert [node.value for node in result.target_tree.nodes if node.node_type == "symbol"] == ["x", "+", "y"]


def test_target_tree_represents_middle_delimiter_inside_fence() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\left\lVert x \middle| y \right\rVert", row_id="middle")

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    assert any(node.node_type == "fence" for node in result.target_tree.nodes)
    assert any(
        node.node_type == "symbol" and node.attrs.get("delimiter_role") == "middle" and node.value == "|"
        for node in result.target_tree.nodes
    )


def test_target_tree_represents_stacked_relations_with_mclass_and_under_over() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\overset{a}{=} b", row_id="stacked", display_mode=True)

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    assert any(node.node_type == "group" and node.attrs.get("atom_class") == "mrel" for node in result.target_tree.nodes)
    assert any(node.node_type == "under_over" for node in result.target_tree.nodes)


def test_target_tree_records_equation_tags_from_parser_array_tags() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(
        r"\begin{equation}\tag{1}a=b\end{equation}",
        row_id="tag",
        display_mode=True,
    )

    assert result.target_tree is not None, result.warnings
    assert not _unsupported_warnings(result.warnings)
    tags = [node for node in result.target_tree.nodes if node.node_type == "equation_number"]
    assert tags
    assert tags[0].value == "(1)"


def test_target_tree_records_genfrac_delimiter_evidence() -> None:
    result = TinyBDTargetTreeBuilder().build_from_latex(r"\binom{n}{k}+\genfrac{[}{]}{0pt}{}{n}{k}", row_id="genfrac")

    assert result.target_tree is not None, result.warnings
    fractions = [node for node in result.target_tree.nodes if node.node_type == "fraction"]
    assert [node.attrs["has_bar_line"] for node in fractions] == [False, False]
    assert fractions[0].attrs["left_delimiter"] == "("
    assert fractions[0].attrs["right_delimiter"] == ")"
    assert fractions[1].attrs["left_delimiter"] == "["
    assert fractions[1].attrs["right_delimiter"] == "]"


def test_target_tree_represents_braces_and_extensible_arrows() -> None:
    builder = TinyBDTargetTreeBuilder()
    brace = builder.build_from_latex(r"\overbrace{x+y}^{n}+\underbrace{a+b}_{m}", row_id="brace", display_mode=True)
    arrow = builder.build_from_latex(r"A \xleftarrow[g]{f} B", row_id="arrow", display_mode=True)

    assert brace.target_tree is not None, brace.warnings
    assert arrow.target_tree is not None, arrow.warnings
    assert not _unsupported_warnings(brace.warnings)
    assert not _unsupported_warnings(arrow.warnings)
    assert sum(1 for node in brace.target_tree.nodes if node.node_type == "accent") == 2
    assert any(node.node_type == "under_over" and node.attrs.get("katex_type") == "xArrow" for node in arrow.target_tree.nodes)


def test_target_tree_represents_layout_artifacts_and_sized_delimiters() -> None:
    layout = TinyBDTargetTreeBuilder().build_from_latex(r"\phantom{x}+\smash{y}+\llap{z}", row_id="layout")
    delimiters = TinyBDTargetTreeBuilder().build_from_latex(r"\bigl( x \bigr)+\Bigl[ y \Bigr]", row_id="delims")

    assert layout.target_tree is not None, layout.warnings
    assert delimiters.target_tree is not None, delimiters.warnings
    assert not _unsupported_warnings(layout.warnings)
    assert not _unsupported_warnings(delimiters.warnings)
    reasons = {node.attrs.get("reason") for node in layout.target_tree.nodes if node.node_type == "artifact"}
    assert {"phantom", "smash", "lap"}.issubset(reasons)
    sized = [node for node in delimiters.target_tree.nodes if node.attrs.get("katex_type") == "delimsizing"]
    assert [node.value for node in sized] == ["(", ")", "[", "]"]


def test_target_tree_keeps_normal_color_as_style_and_blocks_katex_error_color() -> None:
    normal = TinyBDTargetTreeBuilder().build_from_latex(r"\color{red}{x}", row_id="color", display_mode=True)
    unknown = TinyBDTargetTreeBuilder().build_from_latex(r"\unknownmathmacro{x}", row_id="unknown", display_mode=True)

    assert normal.target_tree is not None, normal.warnings
    assert any(node.node_type == "group" and node.attrs.get("role") == "color" for node in normal.target_tree.nodes)
    assert unknown.target_tree is not None
    assert "target_tree_katex_error_color_node" in unknown.warnings
    assert any(
        node.node_type == "artifact" and node.attrs.get("reason") == "katex_error_color"
        for node in unknown.target_tree.nodes
    )


def _unsupported_warnings(warnings: tuple[str, ...]) -> list[str]:
    return [item for item in warnings if item.startswith("target_tree_unsupported_katex_type:")]
