from src.core.tinybdmath_relation_labels import build_relation_label_dataset, label_graph_row


def test_relation_labels_use_geometry_and_latex_tags_without_accepting_formula() -> None:
    row = {
        "row_id": "r1",
        "case": "unit",
        "kind": "inline",
        "page_num": 1,
        "label_latex": "h_{t-1}",
        "coverage_tags": ["inline", "subscript", "script_size_pdf_evidence"],
        "input_hash": "abc",
        "glyph_nodes": [
            {"node_id": "g0000", "text": "h", "is_script_size": False},
            {"node_id": "g0001", "text": "t", "is_script_size": True},
        ],
        "vector_nodes": [],
        "candidate_edges": [
            {"edge_id": "g0000->g0001:subscript_zone", "source": "g0000", "target": "g0001", "hint": "subscript_zone"},
            {"edge_id": "g0001->g0000:far_context", "source": "g0001", "target": "g0000", "hint": "far_context"},
        ],
    }

    labeled = label_graph_row(row)

    labels = {item["edge_id"]: item for item in labeled["edge_labels"]}
    assert labels["g0000->g0001:subscript_zone"]["label"] == "SUB"
    assert labels["g0001->g0000:far_context"]["label"] == "NO_RELATION"
    assert labeled["label_hash"]


def test_relation_label_manifest_counts_labels() -> None:
    result = build_relation_label_dataset([
        {
            "row_id": "r1",
            "label_latex": r"\frac{x}{y}",
            "coverage_tags": ["fraction", "horizontal_rule"],
            "glyph_nodes": [],
            "vector_nodes": [{"node_id": "v0000"}],
            "candidate_edges": [
                {"edge_id": "v0000:fraction_bar_candidate", "source": "v0000", "target": "v0000", "hint": "fraction_bar_candidate"}
            ],
        }
    ])

    assert result.manifest["rows"] == 1
    assert result.manifest["label_counts"]["FRACTION_BAR"] == 1


def test_relation_labels_do_not_promote_fraction_from_coverage_tag_without_mathml_hint() -> None:
    labeled = label_graph_row(
        {
            "row_id": "r_no_hint",
            "label_latex": "x",
            "coverage_tags": ["fraction", "horizontal_rule"],
            "glyph_nodes": [],
            "vector_nodes": [{"node_id": "v0000"}],
            "candidate_edges": [
                {"edge_id": "v0000:fraction_bar_candidate", "source": "v0000", "target": "v0000", "hint": "fraction_bar_candidate"}
            ],
        },
        mathml={"relation_hints": {}, "node_counts": {}, "warnings": []},
    )

    assert labeled["edge_labels"][0]["label"] == "IGNORE"


def test_relation_labels_mark_radical_body_candidate() -> None:
    labeled = label_graph_row(
        {
            "row_id": "r2",
            "label_latex": r"\sqrt{d_k}",
            "coverage_tags": ["radical"],
            "glyph_nodes": [
                {"node_id": "g0", "text": "√"},
                {"node_id": "g1", "text": "d"},
            ],
            "vector_nodes": [],
            "candidate_edges": [
                {"edge_id": "g0->g1:radical_body_candidate", "source": "g0", "target": "g1", "hint": "radical_body_candidate"},
            ],
        }
    )

    assert labeled["edge_labels"][0]["label"] == "RADICAL_BODY"
