from src.core.latex_mathml_extractor import KaTeXMathMLExtractor, extract_many


def test_katex_mathml_extractor_reports_structure_hints() -> None:
    extractor = KaTeXMathMLExtractor()
    result = extractor.extract(r"\frac{x_i}{\sqrt{y}}")

    assert result.schema_version == "latex_mathml_katex_v1"
    assert result.input_hash
    if extractor.available:
        assert result.parser_version
        assert "MathML" in result.mathml
        assert result.relation_hints["FRACTION_BAR"] == 1
        assert result.relation_hints["SUB"] == 1
        assert result.relation_hints["RADICAL_BODY"] == 1
    else:
        assert "katex_node_runtime_unavailable" in result.warnings

def test_extract_many_keeps_source_out_of_production_contract() -> None:
    rows, manifest = extract_many([
        {"row_id": "r1", "case": "unit", "kind": "inline", "label_latex": "x_i", "input_hash": "abc"}
    ])

    assert manifest["schema_version"] == "latex_mathml_katex_manifest_v1"
    assert rows[0]["mathml_extraction"]["latex"] == "x_i"
    assert "Production born-digital parsing must not read source LaTeX." in manifest["notes"]


def test_katex_extractor_sanitizes_cyclic_ast_metadata_for_equation_tags() -> None:
    extractor = KaTeXMathMLExtractor()
    result = extractor.extract(r"\begin{equation}\tag{1}a=b\end{equation}", display_mode=True)

    if extractor.available:
        assert not any("Converting circular structure" in item for item in result.warnings)
        assert result.parse_tree
        assert result.node_counts["array"] == 1
    else:
        assert "katex_node_runtime_unavailable" in result.warnings
