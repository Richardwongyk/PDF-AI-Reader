from src.core.tinybdmath_alignment import TinyBDAlignmentBuilder
from src.core.tinybdmath_cslt_schema import CSLTBuilder
from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder
from tools.tinybdmath_audit_structure_scope import audit_structure_scope_rows


def test_structure_scope_audit_marks_ready_rows_and_coverage_tags() -> None:
    graph_rows = [_graph_row("script", ["h", "t", "-", "1"], "h_{t-1}")]
    target_rows, _manifest = TinyBDTargetTreeBuilder().build_rows(graph_rows)
    alignment_rows, _alignment_manifest = TinyBDAlignmentBuilder().align_rows(graph_rows, target_rows)

    report = audit_structure_scope_rows(
        graph_rows,
        target_rows=target_rows,
        alignment_rows=alignment_rows,
        min_hard_rate=0.70,
    )

    row = report["row_audits"][0]
    assert row["bucket"] == "ready_for_model"
    assert "script" in row["coverage_tags"]
    assert "sequence" in row["coverage_tags"]
    assert row["checks"]["pdf_evidence"] == "present"
    assert report["bucket_counts"]["ready_for_model"] == 1


def test_structure_scope_audit_separates_schema_and_identity_blockers() -> None:
    graph_rows = [
        _graph_row("schema", ["x"], "x"),
        _graph_row("identity", ["?"], r"\sqrt{d_k}"),
    ]
    unsupported_tree = _unsupported_target("schema", "x")
    radical_tree = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt{d_k}", row_id="identity").to_json()
    target_rows = [unsupported_tree, radical_tree]
    alignment_rows, _alignment_manifest = TinyBDAlignmentBuilder().align_rows(graph_rows, target_rows)

    report = audit_structure_scope_rows(
        graph_rows,
        target_rows=target_rows,
        alignment_rows=alignment_rows,
        min_hard_rate=0.70,
    )
    rows = {row["row_id"]: row for row in report["row_audits"]}

    assert rows["schema"]["bucket"] == "needs_schema_extension"
    assert "unsupported_target_artifact:unsupported_katex_node" in rows["schema"]["reasons"]
    assert rows["schema"]["parser_evidence"]["katex_type_counts"]["unsupported"] == 1
    assert rows["identity"]["bucket"] == "needs_identity_repair"
    assert "unmatched_target_nodes" in rows["identity"]["reasons"]


def test_structure_scope_audit_routes_images_and_unsupported_objects() -> None:
    graph_rows = [
        {
            "row_id": "image",
            "input_hash": "image-hash",
            "label_latex": "x",
            "candidate_kind": "image_formula",
            "document_kind": "mixed",
            "glyph_nodes": [],
        },
        {
            **_graph_row("diagram", ["x"], "x"),
            "candidate_kind": "diagram",
            "domain": "diagram",
        },
    ]

    report = audit_structure_scope_rows(graph_rows, min_hard_rate=0.70)
    rows = {row["row_id"]: row for row in report["row_audits"]}

    assert rows["image"]["bucket"] == "needs_image_mfr"
    assert rows["image"]["route"]["recommended_route"] == "image_mfr"
    assert rows["diagram"]["bucket"] == "route_unsupported"
    assert "route_candidate_kind:diagram" in rows["diagram"]["reasons"]


def test_structure_scope_audit_counts_standard_m0_parser_structures() -> None:
    graph_rows = [
        _graph_row("limits", [r"\sum", "i", "=", "1", "n"], r"\sum_{i=1}^{n}"),
        _graph_row("operator", ["f", "o", "o"], r"\operatorname{foo}"),
        _graph_row("accent", ["x", "y"], r"\hat{x}+\overline{y}"),
        _graph_row("fence", [r"\lVert", "x", "|", "y", r"\rVert"], r"\left\lVert x \middle| y \right\rVert"),
        _graph_row("box", ["x"], r"\boxed{x}"),
        _graph_row("layout", ["x", "y"], r"\phantom{x}+\color{red}{y}"),
        _graph_row(
            "tag",
            ["a", "=", "b"],
            r"\begin{equation}\tag{1}a=b\end{equation}",
            kind="display",
        ),
    ]

    report = audit_structure_scope_rows(graph_rows, min_hard_rate=0.0)

    coverage = report["coverage_counts"]
    assert coverage["under_over"] >= 1
    assert coverage["operator"] >= 1
    assert coverage["accent_annotation"] >= 1
    assert coverage["fence"] >= 1
    assert coverage["enclosure"] >= 1
    assert coverage["equation_tag"] >= 1
    assert coverage["spacing_artifact"] >= 1
    assert coverage["style_variant"] >= 1


def _graph_row(row_id: str, texts: list[str], label: str, *, kind: str = "inline") -> dict:
    glyphs = []
    for index, text in enumerate(texts):
        glyphs.append(
            {
                "node_id": f"g{index:04d}",
                "node_type": "glyph",
                "text": text,
                "unicode": text,
                "latex": text,
                "font": "CMR",
                "bbox": [float(index * 10), 0.0, float(index * 10 + 5), 8.0],
                "is_math_font": True,
            }
        )
    return {
        "row_id": row_id,
        "input_hash": row_id + "-hash",
        "kind": kind,
        "label_latex": label,
        "glyph_nodes": glyphs,
        "vector_nodes": [],
        "document_kind": "born_digital",
        "candidate_kind": "inline_math",
    }


def _simple_symbol_target(row_id: str, latex: str, symbol: str) -> dict:
    builder = CSLTBuilder()
    root = builder.add_node("group", attrs={"role": "root"})
    child = builder.add_node("symbol", value=symbol, latex=symbol)
    builder.add_edge(root, child, "child", order=0)
    return {
        "row_id": row_id,
        "latex": latex,
        "target_tree": builder.build(root).to_json(),
        "parser_summary": {"katex_type_counts": {"mathord": 1}},
        "warnings": [],
    }


def _unsupported_target(row_id: str, latex: str) -> dict:
    builder = CSLTBuilder()
    root = builder.add_node("group", attrs={"role": "root"})
    artifact = builder.add_node("artifact", attrs={"reason": "unsupported_katex_node", "katex_type": "unsupported"})
    builder.add_edge(root, artifact, "child", order=0)
    return {
        "row_id": row_id,
        "latex": latex,
        "target_tree": builder.build(root).to_json(),
        "parser_summary": {"katex_type_counts": {"unsupported": 1}, "mathml_tag_counts": {"mrow": 1}},
        "warnings": ["target_tree_unsupported_katex_type:unsupported"],
    }
