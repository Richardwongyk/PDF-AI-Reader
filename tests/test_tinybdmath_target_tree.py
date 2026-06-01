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
