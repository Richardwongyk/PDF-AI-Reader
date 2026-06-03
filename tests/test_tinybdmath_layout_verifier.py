from src.core.tinybdmath_latex_decoder import decode_latex_candidate


def test_layout_verifier_passes_fraction_with_supported_structure() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "a", "bbox": [2, 0, 7, 6]},
            {"node_id": "g1", "latex": "b", "bbox": [2, 10, 7, 16]},
        ],
        {
            "selected_relations": [
                {"source": "v0", "target": "v0", "relation": "FRACTION_BAR", "confidence": 0.96},
                {"source": "v0", "target": "g0", "relation": "ABOVE", "confidence": 0.93},
                {"source": "v0", "target": "g1", "relation": "BELOW", "confidence": 0.94},
            ],
            "verifier_warnings": [],
        },
        vectors=[{"node_id": "v0", "bbox": [0, 7, 12, 7.5]}],
        fallback_text="a/b",
    )

    assert decoded.latex == r"\frac{a}{b}"
    assert decoded.layout_status == "pass"
    assert decoded.abstain is False
    assert decoded.layout_verification["relation_node_coverage"] == 1.0


def test_layout_verifier_abstains_multi_glyph_without_model_relations() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "y", "bbox": [10, 0, 18, 10]},
        ],
        {"selected_relations": [], "verifier_warnings": []},
        fallback_text="xy",
    )

    assert decoded.latex == "xy"
    assert decoded.layout_status == "abstain"
    assert decoded.abstain is True
    assert "layout_no_selected_relations" in decoded.warnings


def test_layout_verifier_keeps_single_glyph_linear_candidate() -> None:
    decoded = decode_latex_candidate(
        [{"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]}],
        {"selected_relations": [], "verifier_warnings": [], "abstain": True},
        fallback_text="x",
    )

    assert decoded.latex == "x"
    assert decoded.layout_status == "pass"
    assert decoded.abstain is False
    assert decoded.confidence > 0


def test_layout_verifier_passes_left_attachment_structure() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "a", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "latex": "b", "bbox": [5, 4, 9, 14]},
            {"node_id": "g2", "latex": "X", "bbox": [14, 2, 22, 12]},
        ],
        {
            "selected_relations": [
                {"source": "g2", "target": "g0", "relation": "PRE_SUP", "confidence": 0.92},
                {"source": "g2", "target": "g1", "relation": "PRE_SUB", "confidence": 0.92},
            ],
            "verifier_warnings": [],
        },
        fallback_text="abX",
    )

    assert decoded.latex == r"{}_{b}^{a}X"
    assert decoded.layout_status == "pass"
    assert decoded.layout_verification["supported_relation_count"] == 2


def test_layout_verifier_passes_radical_index_structure() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": r"\sqrt", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "3", "bbox": [2, -4, 5, 1]},
            {"node_id": "g2", "latex": "x", "bbox": [9, 1, 15, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g2", "relation": "RADICAL_BODY", "confidence": 0.92},
                {"source": "g0", "target": "g1", "relation": "RADICAL_INDEX", "confidence": 0.91},
            ],
            "verifier_warnings": [],
        },
        fallback_text="3sqrtx",
    )

    assert decoded.latex == r"\sqrt[3]{x}"
    assert decoded.layout_status == "pass"
    assert decoded.layout_verification["supported_relation_count"] == 2


def test_layout_verifier_supports_enclosure_and_equation_tag_relations() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [4, 4, 8, 10]},
            {"node_id": "g1", "latex": "(1)", "bbox": [40, 4, 55, 10]},
        ],
        {
            "selected_relations": [
                {"source": "v0", "target": "g0", "relation": "ENCLOSURE_BODY", "confidence": 0.91},
                {"source": "g0", "target": "g1", "relation": "EQUATION_TAG", "confidence": 0.90},
            ],
            "verifier_warnings": [],
        },
        vectors=[{"node_id": "v0", "bbox": [0, 1, 15, 1.4]}],
        fallback_text="x(1)",
    )

    assert decoded.latex == "x"
    assert decoded.layout_status == "review"
    assert decoded.layout_verification["supported_relation_count"] == 2
    assert "layout_unsupported_relation_labels" not in decoded.warnings
    assert "layout_decoder_enclosure_body_unwrapped" in decoded.warnings


def test_layout_verifier_abstains_when_graph_parser_model_is_missing() -> None:
    decoded = decode_latex_candidate(
        [{"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]}],
        {
            "selected_relations": [],
            "verifier_warnings": [],
            "abstain": True,
            "model_version": "tinybdmath_no_graph_parser_model_v0",
        },
        fallback_text="x",
    )

    assert decoded.layout_status == "abstain"
    assert decoded.abstain is True
    assert "layout_graph_parser_model_missing" in decoded.warnings
