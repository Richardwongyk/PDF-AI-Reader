from src.core.tinybdmath_structural_candidate import (
    SLT_SKELETON_VERSION,
    STRUCTURAL_CANDIDATE_SCHEMA_VERSION,
    build_structural_candidate,
    build_structural_candidates,
)


def test_structural_candidate_selects_high_confidence_relations_only() -> None:
    candidate = build_structural_candidate(
        {
            "row_id": "r1",
            "case": "unit",
            "kind": "inline",
            "page_num": 1,
            "graph_input_hash": "abc",
            "model_version": "m",
            "relation_scores": [
                _score("e1", "g0", "g1", "subscript_zone", "SUB", 0.92),
                _score("e2", "g0", "g2", "far_context", "NO_RELATION", 0.99),
                _score("e3", "g1", "g2", "right_neighbor", "HORIZONTAL", 0.45),
            ],
            "verifier_warnings": [],
        },
        min_confidence=0.70,
    )

    payload = candidate.to_json()
    assert payload["schema_version"] == STRUCTURAL_CANDIDATE_SCHEMA_VERSION
    assert payload["candidate_only"] is True
    assert payload["selected_relations"][0]["relation"] == "SUB"
    assert payload["slt_skeleton"]["schema_version"] == SLT_SKELETON_VERSION
    assert payload["slt_skeleton"]["node_count"] >= 2
    assert payload["verifier_report"]["passed_for_accepted"] is False
    assert "accepted LaTeX requires external decoder" in payload["verifier_report"]["accepted_blocker"]
    assert payload["abstain"] is False


def test_structural_candidate_reports_horizontal_rule_ambiguity() -> None:
    candidate = build_structural_candidate(
        {
            "row_id": "r1",
            "relation_scores": [
                _score("e1", "v0", "g1", "fraction_bar_candidate", "FRACTION_BAR", 0.91),
                _score("e2", "v0", "g2", "above_rule_candidate", "ABOVE", 0.87),
                _score("e3", "v0", "g3", "below_rule_candidate", "BELOW", 0.86),
            ],
        },
        min_confidence=0.70,
    )

    payload = candidate.to_json()
    assert "horizontal_rule_ambiguous" in payload["verifier_warnings"]
    assert "ambiguous_rule_blocks_slt" in payload["verifier_warnings"]
    assert payload["abstain"] is True


def test_structural_candidate_selects_radical_body_relation() -> None:
    candidate = build_structural_candidate(
        {
            "row_id": "r2",
            "relation_scores": [
                _score("e1", "g0", "g1", "radical_body_candidate", "RADICAL_BODY", 0.91),
            ],
        },
        min_confidence=0.70,
    )

    payload = candidate.to_json()
    assert payload["selected_relations"][0]["relation"] == "RADICAL_BODY"
    assert payload["abstain"] is False


def test_structural_candidate_manifest_is_candidate_only() -> None:
    _rows, manifest = build_structural_candidates([
        {"row_id": "r", "relation_scores": [_score("e", "g0", "g1", "right_neighbor", "HORIZONTAL", 0.9)]}
    ])

    assert manifest["candidate_only"] is True
    assert manifest["accepted_latex_emitted"] is False
    assert manifest["rows"] == 1


def test_structural_candidate_detects_multiple_parent_conflict() -> None:
    candidate = build_structural_candidate(
        {
            "row_id": "r1",
            "relation_scores": [
                _score("e1", "g0", "g2", "right_neighbor", "HORIZONTAL", 0.91),
                _score("e2", "g1", "g2", "superscript_zone", "SUP", 0.93),
            ],
        },
        min_confidence=0.70,
    )

    payload = candidate.to_json()
    assert "slt_node_has_multiple_parents" in payload["verifier_warnings"]
    assert payload["verifier_report"]["checks"]["multi_parent_nodes"] == ["g2"]


def _score(edge_id: str, source: str, target: str, hint: str, relation: str, confidence: float) -> dict:
    return {
        "edge_id": edge_id,
        "source": source,
        "target": target,
        "hint": hint,
        "predicted_relation": relation,
        "confidence": confidence,
        "probabilities": {relation: confidence},
    }
