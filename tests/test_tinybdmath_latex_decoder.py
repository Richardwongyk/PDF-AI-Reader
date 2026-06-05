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


def test_decoder_serializes_radical_index_relations_from_model_edges() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": r"\sqrt", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "3", "bbox": [2, -4, 5, 1]},
            {"node_id": "g2", "latex": "x", "bbox": [9, 1, 15, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g2", "relation": "RADICAL_BODY", "confidence": 0.93},
                {"source": "g0", "target": "g1", "relation": "RADICAL_INDEX", "confidence": 0.91},
            ],
            "verifier_warnings": [],
        },
        fallback_text="3sqrtx",
    )

    assert decoded.latex == r"\sqrt[3]{x}"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_serializes_under_over_relations_from_model_edges() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": r"\sum", "bbox": [0, 4, 10, 14]},
            {"node_id": "g1", "latex": "i", "bbox": [2, 14, 7, 20]},
            {"node_id": "g2", "latex": "n", "bbox": [2, 0, 7, 5]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "UNDER", "confidence": 0.91},
                {"source": "g0", "target": "g2", "relation": "OVER", "confidence": 0.92},
            ],
            "verifier_warnings": [],
        },
        fallback_text="sum",
    )

    assert decoded.latex == r"\sum_{i}^{n}"
    assert decoded.confidence > 0


def test_decoder_serializes_left_attachment_relations_from_model_edges() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "a", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "latex": "b", "bbox": [5, 4, 9, 14]},
            {"node_id": "g2", "latex": "X", "bbox": [14, 2, 22, 12]},
        ],
        {
            "selected_relations": [
                {"source": "g2", "target": "g0", "relation": "PRE_SUP", "confidence": 0.93},
                {"source": "g2", "target": "g1", "relation": "PRE_SUB", "confidence": 0.92},
            ],
            "verifier_warnings": [],
        },
        fallback_text="abX",
    )

    assert decoded.latex == r"{}_{b}^{a}X"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_keeps_incomplete_fence_evidence_candidate_only() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "(", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "latex": "x", "bbox": [5, 0, 10, 10]},
            {"node_id": "g2", "latex": ")", "bbox": [11, 0, 15, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "FENCE_BODY", "confidence": 0.95},
            ],
            "verifier_warnings": [],
        },
        fallback_text="(x)",
    )

    assert decoded.latex == "(x)"
    assert decoded.candidate_only is True
    assert decoded.accepted is False
    assert "decoder_unsupported_relation_labels" not in decoded.warnings


def test_decoder_serializes_complete_fence_relations_from_model_edges() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "(", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "latex": "x", "bbox": [5, 0, 10, 10]},
            {"node_id": "g2", "latex": ")", "bbox": [11, 0, 15, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g1", "target": "g0", "relation": "FENCE_OPEN", "confidence": 0.95},
                {"source": "g1", "target": "g2", "relation": "FENCE_CLOSE", "confidence": 0.95},
                {"source": "g0", "target": "g1", "relation": "FENCE_BODY", "confidence": 0.91},
                {"source": "g2", "target": "g1", "relation": "FENCE_BODY", "confidence": 0.91},
            ],
            "verifier_warnings": [],
        },
        fallback_text="(x)",
    )

    assert decoded.latex == "(x)"
    assert decoded.layout_verification["constrained_decode"]["status"] == "pass"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_serializes_text_run_relations_as_text_group() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "unicode": "m", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "unicode": "o", "bbox": [5, 0, 9, 10]},
            {"node_id": "g2", "unicode": "d", "bbox": [10, 0, 14, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
                {"source": "g1", "target": "g2", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
            ],
            "node_predictions": [
                {"node_id": "g0", "label": "TEXT", "confidence": 0.90},
                {"node_id": "g1", "label": "TEXT", "confidence": 0.90},
                {"node_id": "g2", "label": "TEXT", "confidence": 0.90},
            ],
            "verifier_warnings": [],
        },
        fallback_text="mod",
    )

    assert decoded.latex == r"\text{mod}"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_serializes_operator_text_run_from_model_node_label() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "unicode": "a", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "unicode": "b", "bbox": [5, 0, 9, 10]},
            {"node_id": "g2", "unicode": "c", "bbox": [10, 0, 14, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
                {"source": "g1", "target": "g2", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
            ],
            "node_predictions": [
                {"node_id": "g0", "label": "OPERATOR", "confidence": 0.90},
                {"node_id": "g1", "label": "OPERATOR", "confidence": 0.90},
                {"node_id": "g2", "label": "OPERATOR", "confidence": 0.90},
            ],
            "verifier_warnings": [],
        },
        fallback_text="abc",
    )

    assert decoded.latex == r"\operatorname{abc}"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_requires_high_confidence_text_run_before_wrapping() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "unicode": "m", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "unicode": "o", "bbox": [5, 0, 9, 10]},
            {"node_id": "g2", "unicode": "d", "bbox": [10, 0, 14, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
                {"source": "g1", "target": "g2", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
            ],
            "node_predictions": [
                {"node_id": "g0", "label": "TEXT", "confidence": 0.80},
                {"node_id": "g1", "label": "TEXT", "confidence": 0.80},
                {"node_id": "g2", "label": "TEXT", "confidence": 0.80},
            ],
            "verifier_warnings": [],
        },
        fallback_text="mod",
    )

    assert decoded.latex == "mod"
    assert r"\text{" not in decoded.latex
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_keeps_text_run_relations_linear_without_text_node_evidence() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "latex": "+", "bbox": [5, 0, 9, 10]},
            {"node_id": "g2", "latex": "y", "bbox": [10, 0, 14, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
                {"source": "g1", "target": "g2", "relation": "TEXT_RUN_NEXT", "confidence": 0.94},
            ],
            "node_predictions": [
                {"node_id": "g0", "label": "SYMBOL", "confidence": 0.80},
                {"node_id": "g1", "label": "OPERATOR", "confidence": 0.80},
                {"node_id": "g2", "label": "SYMBOL", "confidence": 0.80},
            ],
            "verifier_warnings": [],
        },
        fallback_text="x+y",
    )

    assert decoded.latex == "x+y"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_serializes_matrix_row_relations_from_model_edges() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "a", "bbox": [0, 0, 4, 10]},
            {"node_id": "g1", "latex": "b", "bbox": [10, 0, 14, 10]},
            {"node_id": "g2", "latex": "c", "bbox": [0, 14, 4, 24]},
            {"node_id": "g3", "latex": "d", "bbox": [10, 14, 14, 24]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g2", "relation": "MATRIX_ROW", "confidence": 0.92},
                {"source": "g0", "target": "g1", "relation": "MATRIX_CELL", "confidence": 0.92},
                {"source": "g2", "target": "g3", "relation": "MATRIX_CELL", "confidence": 0.92},
            ],
            "verifier_warnings": [],
        },
        fallback_text="abcd",
    )

    assert decoded.latex == r"\begin{matrix}a&b\\c&d\end{matrix}"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_keeps_enclosure_body_candidate_without_unsupported_warning() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [4, 4, 8, 10]},
        ],
        {
            "selected_relations": [
                {"source": "v0", "target": "g0", "relation": "ENCLOSURE_BODY", "confidence": 0.91},
            ],
            "verifier_warnings": [],
        },
        vectors=[{"node_id": "v0", "bbox": [0, 1, 15, 1.4]}],
        fallback_text="x",
    )

    assert decoded.latex == "x"
    assert "decoder_unsupported_relation_labels" not in decoded.warnings
    assert "decoder_enclosure_body_unwrapped" in decoded.warnings
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_keeps_equation_tag_in_evidence_not_body_latex() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [0, 0, 5, 8]},
            {"node_id": "g1", "latex": "(1)", "bbox": [40, 0, 55, 8]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "EQUATION_TAG", "confidence": 0.90},
            ],
            "verifier_warnings": [],
        },
        fallback_text="x(1)",
    )

    assert decoded.latex == "x"
    assert "decoder_unsupported_relation_labels" not in decoded.warnings
    assert decoded.canonical_cslt["relations"][0]["relation"] == "EQUATION_TAG"
    assert decoded.candidate_only is True
    assert decoded.accepted is False


def test_decoder_emits_n_best_latex_candidates_from_model_relation_alternatives() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "i", "bbox": [9, 4, 12, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "SUB", "confidence": 0.91},
            ],
            "relation_alternatives": [
                {
                    "source": "g0",
                    "target": "g1",
                    "alternatives": [
                        {"relation": "SUB", "confidence": 0.91},
                        {"relation": "SUP", "confidence": 0.42},
                    ],
                }
            ],
            "verifier_warnings": [],
        },
        fallback_text="xi",
    )

    assert decoded.latex == "x_{i}"
    assert [item["latex"] for item in decoded.latex_candidates] == ["x_{i}", "x^{i}"]
    assert len(decoded.n_best_cslt) == 2
    assert all(item["candidate_only"] is True for item in decoded.latex_candidates)
    assert all(item["accepted"] is False for item in decoded.latex_candidates)
    assert decoded.latex_candidates[0]["selection_blockers"] == []
    assert "not_rank_one_selected_candidate" in decoded.latex_candidates[1]["selection_blockers"]
    assert decoded.latex_candidates[1]["layout_verification"]["candidate_only"] is True
    assert decoded.manual_review_recommendation["auto_accept_allowed"] is False
    assert decoded.manual_review_recommendation["accepted"] is False
    assert decoded.manual_review_recommendation["candidate_only"] is True


def test_decoder_keeps_duplicate_latex_alternative_structure_evidence() -> None:
    decoded = decode_latex_candidate(
        [
            {"node_id": "g0", "latex": "a", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "b", "bbox": [10, 0, 18, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "HORIZONTAL", "confidence": 0.95},
                {"source": "g1", "target": "g0", "relation": "HORIZONTAL", "confidence": 0.40},
            ],
            "verifier_warnings": [],
        },
        fallback_text="ab",
    )

    assert decoded.latex == "ab"
    assert decoded.latex_candidates[0]["latex"] == "ab"
    assert decoded.latex_candidates[0]["layout_status"] == "abstain"
    assert "layout_abstain" in decoded.latex_candidates[0]["selection_blockers"]
    assert decoded.latex_candidates[0]["alternative_structure_evidence"]
    evidence = decoded.latex_candidates[0]["alternative_structure_evidence"][0]
    assert evidence["cslt_candidate_id"] == "acyclic_projection"
    assert evidence["rank"] == 2
    assert evidence["layout_status"] == "pass"
    assert decoded.verifier_ranked_candidates[0]["cslt_candidate_id"] == "acyclic_projection"
    assert decoded.verifier_ranked_candidates[0]["layout_status"] == "pass"
    assert decoded.verifier_ranked_candidates[0]["verifier_score"] > decoded.verifier_ranked_candidates[1]["verifier_score"]
    assert "relation_node_coverage" in decoded.verifier_ranked_candidates[0]["ranking_features"]
    assert decoded.preferred_candidate["recommended_rank"] == 1
    assert decoded.preferred_candidate["source"] == "selected_structural_candidate"
    assert decoded.preferred_candidate["requires_cloud_semantic_review"] is True
    assert "cloud_review_required_for_layout_abstain" in decoded.preferred_candidate["selection_blockers"]
    assert decoded.manual_review_recommendation["recommended_rank"] == 2
    assert decoded.manual_review_recommendation["cslt_candidate_id"] == "acyclic_projection"
    assert "manual_review_required_for_non_rank_one_candidate" in decoded.manual_review_recommendation["selection_blockers"]
    assert evidence["candidate_only"] is True
    assert evidence["accepted"] is False
