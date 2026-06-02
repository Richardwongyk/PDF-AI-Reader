from src.core.tinybdmath_latex_decoder import decode_latex_candidate


def test_decoder_builds_subscript_candidate_from_selected_relations() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "h", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "t", "bbox": [8, 4, 11, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "SUB", "confidence": 0.98},
            ],
            "verifier_warnings": [],
        },
        fallback_text="ht",
    )

    assert decoded.latex == "h_{t}"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_uses_ordered_glyphs_when_no_relations() -> None:
    decoded = decode_latex_candidate(
        [{"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]}],
        {"selected_relations": []},
        fallback_text="fallback",
    )

    assert decoded.latex == "x"
    assert "decoder_no_selected_relations" in decoded.warnings


def test_decoder_uses_model_spacing_node_predictions_before_linear_fallback() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": r"\,", "bbox": [8, 0, 9, 10]},
            {"node_id": "g2", "latex": "y", "bbox": [10, 0, 18, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "HORIZONTAL", "confidence": 0.95},
                {"source": "g1", "target": "g2", "relation": "HORIZONTAL", "confidence": 0.95},
            ],
            "node_predictions": [
                {"node_id": "g1", "label": "SPACING", "confidence": 0.95},
            ],
            "node_filter_threshold": 0.90,
        },
        fallback_text="x y",
    )

    assert decoded.latex == "xy"
    assert "decoder_filtered_spacing_nodes" in decoded.warnings


def test_decoder_maps_unicode_math_glyphs_to_standard_latex() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "unicode": "⊆", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "unicode": "Ω", "bbox": [10, 0, 18, 10]},
            {"node_id": "g2", "unicode": "⋆", "bbox": [20, 0, 28, 10]},
        ],
        {"selected_relations": []},
        fallback_text="⊆Ω⋆",
    )

    assert decoded.latex == r"\subseteq\Omega\star"
    assert "decoder_no_selected_relations" in decoded.warnings


def test_decoder_builds_fraction_candidate_from_structure_relations() -> None:
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
    assert decoded.accepted is False


def test_decoder_builds_radical_body_candidate() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": r"\sqrt", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "d", "bbox": [8, 1, 14, 10]},
            {"node_id": "g2", "latex": "k", "bbox": [14, 4, 18, 11]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "RADICAL_BODY", "confidence": 0.91},
                {"source": "g1", "target": "g2", "relation": "SUB", "confidence": 0.93},
            ],
            "verifier_warnings": [],
        },
        fallback_text="√dk",
    )

    assert decoded.latex == r"\sqrt{d_{k}}"
