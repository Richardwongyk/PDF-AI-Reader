from tools.tinybdmath_audit_alignment import audit_alignment_rows


def test_alignment_audit_reports_gate_and_failure_buckets() -> None:
    report = audit_alignment_rows(
        [
            {
                "row_id": "good",
                "warnings": [],
                "relation_labels": [{"relation": "SUB", "supervision": "hard"}],
                "structure_labels": [
                    {"role": "TARGET_SCRIPT_GROUP_BOUNDARY_EVIDENCE"},
                    {"role": "TARGET_VECTOR_ROLE_EVIDENCE", "vector_role": "HORIZONTAL_RULE"},
                    {"role": "TARGET_IDENTITY_REPAIR_EVIDENCE"},
                ],
                "ignored_pdf_nodes": [{"reason": "spacing_or_blank"}],
                "unmatched_target_nodes": [],
                "stats": {"hard_alignment_rate": 1.0, "leaf_alignment_rate": 1.0, "relation_counts": {"SUB": 1}},
            },
            {
                "row_id": "bad",
                "warnings": ["alignment_low_hard_coverage"],
                "relation_labels": [],
                "ignored_pdf_nodes": [],
                "unmatched_target_nodes": [{"reason": "target_leaf_unmatched"}],
                "stats": {"hard_alignment_rate": 0.2, "leaf_alignment_rate": 0.4, "relation_counts": {}},
            },
        ],
        min_hard_row_rate=0.70,
    )

    assert report["rows"] == 2
    assert report["warnings"]["alignment_low_hard_coverage"] == 1
    assert report["relation_counts"]["SUB"] == 1
    assert report["structure_counts"]["TARGET_SCRIPT_GROUP_BOUNDARY_EVIDENCE"] == 1
    assert report["vector_role_counts"]["HORIZONTAL_RULE"] == 1
    assert report["rows_with_group_boundary"] == 1
    assert report["rows_with_vector_role"] == 1
    assert report["rows_with_identity_evidence"] == 1
    assert report["ignored_reasons"]["spacing_or_blank"] == 1
    assert report["unmatched_reasons"]["target_leaf_unmatched"] == 1
    assert report["gate"]["passed"] is False
    assert report["top_failures"][0]["row_id"] == "bad"
