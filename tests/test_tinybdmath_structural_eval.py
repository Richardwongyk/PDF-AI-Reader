from src.core.tinybdmath_structural_eval import evaluate_structural_candidates


def test_structural_eval_reports_relation_metrics() -> None:
    report = evaluate_structural_candidates(
        [
            {
                "row_id": "r1",
                "selected_relations": [
                    {"source": "g0", "target": "g1", "relation": "SUB"},
                    {"source": "g1", "target": "g2", "relation": "HORIZONTAL"},
                ],
                "verifier_warnings": ["candidate_warning"],
            }
        ],
        [
            {
                "row_id": "r1",
                "edge_labels": [
                    {"source": "g0", "target": "g1", "label": "SUB", "quality": "medium"},
                    {"source": "g3", "target": "g4", "label": "SUP", "quality": "weak"},
                ],
            }
        ],
    )

    assert report["candidate_only"] is True
    assert report["micro"]["tp"] == 1
    assert report["micro"]["fp"] == 1
    assert report["micro"]["fn"] == 1
    assert report["per_relation"]["SUB"]["f1"] == 1.0
    assert report["warning_counts"]["candidate_warning"] == 1


def test_structural_eval_can_exclude_weak_labels() -> None:
    report = evaluate_structural_candidates(
        [{"row_id": "r", "selected_relations": []}],
        [{"row_id": "r", "edge_labels": [{"source": "a", "target": "b", "label": "SUP", "quality": "weak"}]}],
        include_weak=False,
    )

    assert report["micro"]["fn"] == 0
