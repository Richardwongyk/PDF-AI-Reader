from src.core.tinybdmath_alignment import TinyBDAlignmentBuilder
from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder


def test_alignment_builds_hard_script_and_next_labels() -> None:
    graph_row = _graph_row("r1", ["h", "t", "-", "1"])
    target = TinyBDTargetTreeBuilder().build_from_latex("h_{t-1}", row_id="r1").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_node_alignments"] == 4
    assert result["stats"]["hard_alignment_rate"] == 1.0
    relations = {(item["source"], item["target"], item["relation"]) for item in result["relation_labels"]}
    assert ("g0000", "g0001", "SUB") in relations
    assert ("g0001", "g0002", "NEXT") in relations


def test_alignment_ignores_unmatched_pdf_artifacts_and_tracks_radical_body() -> None:
    graph_row = _graph_row("r2", [r"\sqrt", "d", "k", " "])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt{d_k}", row_id="r2").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_node_alignments"] >= 2
    assert any(item["relation"] == "RADICAL_BODY" for item in result["relation_labels"])
    assert any(item["role"] == "TARGET_RADICAL_MARK_EVIDENCE" for item in result["structure_labels"])
    assert result["ignored_pdf_nodes"]


def test_alignment_tracks_radical_index() -> None:
    graph_row = _graph_row("radical-index", [r"\sqrt", "3", "x"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt[3]{x}", row_id="radical-index").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    relations = {(item["source"], item["target"], item["relation"]) for item in result["relation_labels"]}
    assert ("g0000", "g0001", "RADICAL_INDEX") in relations
    assert ("g0000", "g0002", "RADICAL_BODY") in relations
    radical = [item for item in result["structure_labels"] if item["role"] == "TARGET_RADICAL_MARK_EVIDENCE"][0]
    assert radical["mark_pdf_node_ids"] == ["g0000"]
    assert radical["index_pdf_node_ids"] == ["g0001"]
    assert radical["body_pdf_node_ids"] == ["g0002"]


def test_alignment_records_fraction_structure_labels() -> None:
    graph_row = _graph_row("r5", ["x", "y"])
    graph_row["glyph_nodes"][0]["bbox"] = [0.0, 0.0, 5.0, 8.0]
    graph_row["glyph_nodes"][1]["bbox"] = [0.0, 20.0, 5.0, 28.0]
    graph_row["vector_nodes"] = [{"node_id": "v0000", "bbox": [-2.0, 13.0, 12.0, 13.3]}]
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\frac{x}{y}", row_id="r5").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    labels = [item for item in result["structure_labels"] if item["role"] == "TARGET_FRACTION_SEPARATOR_EVIDENCE"]
    assert labels
    assert labels[0]["above_pdf_node_ids"] == ["g0000"]
    assert labels[0]["below_pdf_node_ids"] == ["g0001"]
    assert any(item["role"] == "TARGET_FRACTION_GROUP_BOUNDARY_EVIDENCE" for item in result["structure_labels"])
    vector_roles = [item for item in result["structure_labels"] if item["role"] == "TARGET_VECTOR_ROLE_EVIDENCE"]
    assert any(item["vector_role"] == "FRACTION_BAR" for item in vector_roles)


def test_alignment_uses_parser_identity_for_tex_command_symbols() -> None:
    graph_row = _graph_row("r3", ["⊆"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\subseteq", row_id="r3").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_alignment_rate"] == 1.0
    assert result["warnings"] == []


def test_alignment_splits_text_run_against_pdf_glyphs() -> None:
    graph_row = _graph_row("r4", ["d", "m", "o", "d", "e", "l"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"d_{\text{model}}", row_id="r4").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_alignment_rate"] == 1.0
    assert any(item["relation"] == "SUB" for item in result["relation_labels"])
    assert any(item["target_node_type"] == "text_run" for item in result["node_alignments"])
    text_runs = [item for item in result["structure_labels"] if item["role"] == "TARGET_TEXT_RUN_EVIDENCE"]
    assert text_runs
    assert text_runs[0]["text_pdf_node_ids"] == ["g0001", "g0002", "g0003", "g0004", "g0005"]


def test_alignment_records_operator_text_run_structure_evidence() -> None:
    graph_row = _graph_row("operator", ["f", "o", "o", "(", "x", ")"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\operatorname{foo}(x)", row_id="operator").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    operator_runs = [
        item
        for item in result["structure_labels"]
        if item["role"] == "TARGET_OPERATOR_TEXT_RUN_EVIDENCE"
    ]
    assert operator_runs
    assert operator_runs[0]["text_pdf_node_ids"] == ["g0000", "g0001", "g0002"]


def test_alignment_turns_named_operator_into_operator_text_run_supervision() -> None:
    graph_row = _graph_row("named-operator", ["l", "i", "m", "x"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\lim_x", row_id="named-operator", display_mode=True).to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    operator_runs = [
        item
        for item in result["structure_labels"]
        if item["role"] == "TARGET_OPERATOR_TEXT_RUN_EVIDENCE"
    ]
    assert operator_runs
    assert operator_runs[0]["text_pdf_node_ids"] == ["g0000", "g0001", "g0002"]
    assert result["stats"]["hard_alignment_rate"] == 1.0


def test_alignment_labels_large_operator_under_over_relations() -> None:
    graph_row = _graph_row("limits", [r"\sum", "i", "=", "1", "n", "x", "i"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\sum_{i=1}^{n} x_i", row_id="limits", display_mode=True).to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    relations = {item["relation"] for item in result["relation_labels"]}
    assert "UNDER" in relations
    assert "OVER" in relations
    assert any(item["role"] == "TARGET_UNDER_OVER_EVIDENCE" for item in result["structure_labels"])


def test_alignment_labels_left_attachment_relations() -> None:
    graph_row = _graph_row("left-script", ["a", "b", "X"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"{}^a_b X", row_id="left-script", display_mode=True).to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    relations = {(item["source"], item["target"], item["relation"]) for item in result["relation_labels"]}
    assert ("g0002", "g0000", "PRE_SUP") in relations
    assert ("g0002", "g0001", "PRE_SUB") in relations
    assert any(item["role"] == "TARGET_LEFT_ATTACHMENT_EVIDENCE" for item in result["structure_labels"])


def test_alignment_records_m0_group_boundary_and_identity_vector_evidence() -> None:
    graph_row = _graph_row("m0", ["h", "t", "-", "1", "⊆"])
    graph_row["vector_nodes"] = [{"node_id": "v0000", "bbox": [0.0, 10.0, 30.0, 10.4]}]
    target = TinyBDTargetTreeBuilder().build_from_latex(r"h_{t-1}\subseteq", row_id="m0").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()
    roles = {item["role"] for item in result["structure_labels"]}

    assert "TARGET_SCRIPT_GROUP_BOUNDARY_EVIDENCE" in roles
    assert "TARGET_GROUP_BOUNDARY_EVIDENCE" in roles
    assert "TARGET_IDENTITY_REPAIR_EVIDENCE" in roles
    assert "TARGET_VECTOR_ROLE_EVIDENCE" in roles
    vector_roles = [item for item in result["structure_labels"] if item["role"] == "TARGET_VECTOR_ROLE_EVIDENCE"]
    assert vector_roles[0]["vector_role"] == "HORIZONTAL_RULE"


def test_alignment_records_standard_structure_evidence_labels() -> None:
    cases = [
        (r"\overline{x}", ["x"], "TARGET_ACCENT_ANNOTATION_EVIDENCE"),
        (r"\left(x\right)", ["(", "x", ")"], "TARGET_FENCE_EVIDENCE"),
        (r"\boxed{x}", ["x"], "TARGET_ENCLOSURE_EVIDENCE"),
        (r"\begin{matrix}x&y\\z&w\end{matrix}", ["x", "y", "z", "w"], "TARGET_MATRIX_GRID_EVIDENCE"),
        (r"\begin{equation}\tag{1}a=b\end{equation}", ["a", "=", "b", "(", "1", ")"], "TARGET_EQUATION_TAG_EVIDENCE"),
    ]

    for latex, glyphs, expected_role in cases:
        target = TinyBDTargetTreeBuilder().build_from_latex(latex, row_id=expected_role, display_mode=True).to_json()
        result = TinyBDAlignmentBuilder().align_row(_graph_row(expected_role, glyphs), target).to_json()
        roles = {item["role"] for item in result["structure_labels"]}

        assert expected_role in roles


def test_alignment_records_matrix_row_and_cell_group_boundaries() -> None:
    target = TinyBDTargetTreeBuilder().build_from_latex(
        r"\begin{matrix}xy&z\\u&vw\end{matrix}",
        row_id="matrix-boundaries",
        display_mode=True,
    ).to_json()
    result = TinyBDAlignmentBuilder().align_row(
        _graph_row("matrix-boundaries", ["x", "y", "z", "u", "v", "w"]),
        target,
    ).to_json()
    roles = {item["role"] for item in result["structure_labels"]}

    assert "TARGET_MATRIX_GROUP_BOUNDARY_EVIDENCE" in roles
    assert "TARGET_MATRIX_ROW_GROUP_BOUNDARY_EVIDENCE" in roles
    assert "TARGET_MATRIX_CELL_GROUP_BOUNDARY_EVIDENCE" in roles


def test_alignment_rows_manifest_counts_missing_targets() -> None:
    rows, manifest = TinyBDAlignmentBuilder().align_rows([_graph_row("missing", ["x"])], [])

    assert len(rows) == 1
    assert rows[0]["warnings"] == ["target_row_missing"]
    assert manifest["warnings"]["target_row_missing"] == 1


def _graph_row(row_id: str, texts: list[str]) -> dict:
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
        "glyph_nodes": glyphs,
        "vector_nodes": [],
    }
