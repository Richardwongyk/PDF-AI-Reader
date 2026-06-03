from src.core.tinybdmath_constrained_decode import constrain_structural_candidate


def test_constrained_decode_passes_supported_structure_graph() -> None:
    result = constrain_structural_candidate(
        [
            {"node_id": "g0", "latex": "h"},
            {"node_id": "g1", "latex": "t"},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "SUB", "confidence": 0.95},
            ],
            "model_version": "graph_parser",
        },
    )

    assert result.status == "pass"
    assert result.blockers == ()
    assert result.relation_node_coverage == 1.0
    assert result.accepted is False


def test_constrained_decode_abstains_on_missing_relation_node() -> None:
    result = constrain_structural_candidate(
        [{"node_id": "g0", "latex": "h"}],
        {
            "selected_relations": [
                {"source": "g0", "target": "missing", "relation": "SUB", "confidence": 0.95},
            ],
            "model_version": "graph_parser",
        },
    )

    assert result.status == "abstain"
    assert "constraint_relation_target_missing" in result.blockers


def test_constrained_decode_abstains_without_graph_parser_model() -> None:
    result = constrain_structural_candidate(
        [{"node_id": "g0", "latex": "x"}],
        {
            "selected_relations": [],
            "model_version": "tinybdmath_no_graph_parser_model_v0",
        },
    )

    assert result.status == "abstain"
    assert "constraint_graph_parser_model_missing" in result.blockers


def test_constrained_decode_treats_fence_relations_as_serialized_schema() -> None:
    result = constrain_structural_candidate(
        [
            {"node_id": "g0", "latex": "("},
            {"node_id": "g1", "latex": "x"},
            {"node_id": "g2", "latex": ")"},
        ],
        {
            "selected_relations": [
                {"source": "g1", "target": "g0", "relation": "FENCE_OPEN", "confidence": 0.90},
                {"source": "g1", "target": "g2", "relation": "FENCE_CLOSE", "confidence": 0.90},
            ],
            "model_version": "graph_parser",
        },
    )

    assert result.status == "pass"
    assert result.blockers == ()
    assert result.nonserialized_relations == ()
    assert "constraint_nonserialized_relation_labels" not in result.warnings


def test_constrained_decode_treats_left_attachment_as_serialized_schema() -> None:
    result = constrain_structural_candidate(
        [
            {"node_id": "g0", "latex": "a"},
            {"node_id": "g1", "latex": "b"},
            {"node_id": "g2", "latex": "X"},
        ],
        {
            "selected_relations": [
                {"source": "g2", "target": "g0", "relation": "PRE_SUP", "confidence": 0.91},
                {"source": "g2", "target": "g1", "relation": "PRE_SUB", "confidence": 0.90},
            ],
            "model_version": "graph_parser",
        },
    )

    assert result.status == "pass"
    assert result.blockers == ()
    assert result.nonserialized_relations == ()
    assert {item["relation"] for item in result.canonical_cslt["relations"]} == {"PRE_SUB", "PRE_SUP"}


def test_constrained_decode_treats_radical_index_as_serialized_schema() -> None:
    result = constrain_structural_candidate(
        [
            {"node_id": "g0", "latex": r"\sqrt"},
            {"node_id": "g1", "latex": "3"},
            {"node_id": "g2", "latex": "x"},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g2", "relation": "RADICAL_BODY", "confidence": 0.92},
                {"source": "g0", "target": "g1", "relation": "RADICAL_INDEX", "confidence": 0.91},
            ],
            "model_version": "graph_parser",
        },
    )

    assert result.status == "pass"
    assert result.blockers == ()
    assert result.nonserialized_relations == ()
    assert {item["relation"] for item in result.canonical_cslt["relations"]} == {"RADICAL_BODY", "RADICAL_INDEX"}


def test_constrained_decode_treats_enclosure_and_equation_tag_as_serialized_schema() -> None:
    result = constrain_structural_candidate(
        [
            {"node_id": "g0", "latex": "x"},
            {"node_id": "g1", "latex": "(1)"},
        ],
        {
            "selected_relations": [
                {"source": "v0", "target": "g0", "relation": "ENCLOSURE_BODY", "confidence": 0.89},
                {"source": "g0", "target": "g1", "relation": "EQUATION_TAG", "confidence": 0.88},
            ],
            "model_version": "graph_parser",
        },
        vectors=[{"node_id": "v0", "bbox": [0, 0, 16, 1]}],
    )

    assert result.status == "pass"
    assert result.blockers == ()
    assert result.nonserialized_relations == ()
    assert {item["relation"] for item in result.canonical_cslt["relations"]} == {"ENCLOSURE_BODY", "EQUATION_TAG"}
    assert all(item["serialized"] is True for item in result.canonical_cslt["relations"])


def test_constrained_decode_outputs_canonical_and_n_best_cslt_from_model_edges() -> None:
    result = constrain_structural_candidate(
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
            "model_version": "graph_parser",
        },
    )

    assert result.canonical_cslt["schema_version"] == "tinybdmath_cslt_candidate_v1"
    assert result.canonical_cslt["relations"][0]["relation"] == "SUB"
    assert result.canonical_cslt["relations"][0]["serialized"] is True
    assert len(result.n_best_cslt) == 2
    assert result.n_best_cslt[0]["relations"][0]["relation"] == "SUB"
    assert result.n_best_cslt[1]["relations"][0]["relation"] == "SUP"
    assert all(item["candidate_only"] is True for item in result.n_best_cslt)


def test_constrained_decode_adds_acyclic_projection_without_replacing_selected_graph() -> None:
    result = constrain_structural_candidate(
        [
            {"node_id": "g0", "latex": "a", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "b", "bbox": [10, 0, 18, 10]},
        ],
        {
            "selected_relations": [
                {"source": "g0", "target": "g1", "relation": "HORIZONTAL", "confidence": 0.95},
                {"source": "g1", "target": "g0", "relation": "HORIZONTAL", "confidence": 0.40},
            ],
            "model_version": "graph_parser",
        },
    )

    assert result.status == "abstain"
    assert "constraint_relation_cycle" in result.blockers
    assert result.n_best_cslt[0]["candidate_id"] == "selected"
    projection = [item for item in result.n_best_cslt if item["candidate_id"] == "acyclic_projection"][0]
    assert len(projection["relations"]) == 1
    assert projection["relations"][0]["source"] == "g0"
    assert projection["relations"][0]["target"] == "g1"
    assert projection["candidate_only"] is True
    assert projection["accepted"] is False
