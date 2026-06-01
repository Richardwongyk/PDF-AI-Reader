import json
from pathlib import Path

from src.core.tinybdmath_edge_baseline import (
    EDGE_FEATURES,
    EDGE_LABELS,
    TinyBDEdgeBaselineModel,
    train_edge_baseline,
)
from src.core.tinybdmath_relation_scorer import TinyBDRelationScorer, score_jsonl_stream_torch, score_rows
from src.core.tinybdmath_relation_scorer import score_rows_torch


def test_relation_scorer_outputs_candidate_only_warnings(tmp_path: Path) -> None:
    samples = [
        _edge_sample("e1", "subscript_zone", "SUB"),
        _edge_sample("e2", "right_neighbor", "HORIZONTAL"),
        _edge_sample("e3", "far_context", "NO_RELATION"),
    ]
    model, _report = train_edge_baseline(samples, epochs=2)
    model_path = tmp_path / "model.json"
    model.save(model_path)
    scorer = TinyBDRelationScorer.from_model_path(model_path, min_confidence=0.0)

    scored = scorer.score_graph_row(
        {
            "row_id": "r1",
            "case": "unit",
            "kind": "inline",
            "page_num": 1,
            "label_latex": "h_t",
            "input_hash": "abc",
            "coverage_tags": ["subscript"],
            "candidate_edges": [
                {
                    "edge_id": "e1",
                    "source": "g0",
                    "target": "g1",
                    "hint": "subscript_zone",
                    "features": samples[0]["features"],
                }
            ],
        }
    )

    payload = scored.to_json()
    assert payload["schema_version"] == "tinybdmath_relation_scores_v1"
    assert payload["relation_scores"]
    assert payload["model_version"] == model.version


def test_score_rows_manifest_is_candidate_only() -> None:
    model, _report = train_edge_baseline([
        _edge_sample("e1", "right_neighbor", "HORIZONTAL"),
        _edge_sample("e2", "far_context", "NO_RELATION"),
    ], epochs=1)

    _rows, manifest = score_rows([
        {"row_id": "r", "candidate_edges": [{"edge_id": "e1", "hint": "right_neighbor", "features": {}}]}
    ], model)

    assert manifest["candidate_only"] is True
    assert manifest["rows"] == 1


def test_relation_scorer_loads_mlp_edge_model_artifact(tmp_path: Path) -> None:
    hidden_row = [0.0 for _ in EDGE_FEATURES]
    hidden_row[list(EDGE_FEATURES).index("hint_subscript_zone")] = 2.0
    output_rows = [[-1.0] for _ in EDGE_LABELS]
    output_rows[list(EDGE_LABELS).index("SUB")] = [3.0]
    model = TinyBDEdgeBaselineModel(
        version="tinybdmath_edge_mlp_test",
        feature_names=EDGE_FEATURES,
        labels=EDGE_LABELS,
        weights=(),
        means=tuple(0.0 for _ in EDGE_FEATURES),
        scales=tuple(1.0 for _ in EDGE_FEATURES),
        train_config={"mode": "unit_test"},
        model_type="mlp_relu",
        hidden_weights=((tuple(hidden_row),),),
        hidden_biases=((0.0,),),
        output_weights=tuple(tuple(row) for row in output_rows),
        output_bias=tuple(0.0 for _ in EDGE_LABELS),
    )
    model_path = tmp_path / "mlp_model.json"
    model.save(model_path)

    scorer = TinyBDRelationScorer.from_model_path(model_path, min_confidence=0.0)
    scored = scorer.score_graph_row(
        {
            "row_id": "r1",
            "candidate_edges": [
                {"edge_id": "e1", "source": "g0", "target": "g1", "hint": "subscript_zone", "features": {}}
            ],
        }
    ).to_json()

    assert scored["model_version"] == "tinybdmath_edge_mlp_test"
    assert scored["relation_scores"][0]["predicted_relation"] == "SUB"


def test_torch_batched_scorer_matches_reference_mlp() -> None:
    hidden_row = [0.0 for _ in EDGE_FEATURES]
    hidden_row[list(EDGE_FEATURES).index("hint_subscript_zone")] = 2.0
    hidden_row[list(EDGE_FEATURES).index("hint_right_neighbor")] = -1.0
    output_rows = [[-1.0] for _ in EDGE_LABELS]
    output_rows[list(EDGE_LABELS).index("SUB")] = [3.0]
    model = TinyBDEdgeBaselineModel(
        version="tinybdmath_edge_mlp_test",
        feature_names=EDGE_FEATURES,
        labels=EDGE_LABELS,
        weights=(),
        means=tuple(0.0 for _ in EDGE_FEATURES),
        scales=tuple(1.0 for _ in EDGE_FEATURES),
        train_config={"mode": "unit_test"},
        model_type="mlp_relu",
        hidden_weights=((tuple(hidden_row),),),
        hidden_biases=((0.0,),),
        output_weights=tuple(tuple(row) for row in output_rows),
        output_bias=tuple(0.0 for _ in EDGE_LABELS),
    )
    rows = [
        {
            "row_id": "r1",
            "case": "unit",
            "kind": "inline",
            "page_num": 1,
            "label_latex": "x_i",
            "input_hash": "abc",
            "coverage_tags": ["subscript"],
            "candidate_edges": [
                {"edge_id": "e1", "source": "g0", "target": "g1", "hint": "subscript_zone", "features": {}},
                {"edge_id": "e2", "source": "g0", "target": "g2", "hint": "right_neighbor", "features": {}},
            ],
        }
    ]

    reference, reference_manifest = score_rows(rows, model, min_confidence=0.0, max_edges=8)
    fast, fast_manifest = score_rows_torch(rows, model, min_confidence=0.0, max_edges=8, batch_rows=4)

    assert fast_manifest["torch_batched"] is True
    assert fast_manifest["rows"] == reference_manifest["rows"]
    assert fast_manifest["relation_scores"] == reference_manifest["relation_scores"]
    assert fast == reference


def test_torch_compact_scorer_keeps_decode_fields_without_probabilities() -> None:
    model, _report = train_edge_baseline([
        _edge_sample("e1", "subscript_zone", "SUB"),
        _edge_sample("e2", "right_neighbor", "HORIZONTAL"),
        _edge_sample("e3", "far_context", "NO_RELATION"),
    ], epochs=2)
    rows = [
        {
            "row_id": "r1",
            "coverage_tags": ["subscript"],
            "candidate_edges": [
                {
                    "edge_id": "e1",
                    "source": "g0",
                    "target": "g1",
                    "hint": "subscript_zone",
                    "features": {"dx_over_height": 0.2, "dy_over_height": 0.6, "x_overlap": 0.1},
                }
            ],
        }
    ]

    compact, manifest = score_rows_torch(
        rows,
        model,
        min_confidence=0.0,
        max_edges=8,
        batch_rows=4,
        compact_output=True,
    )

    score = compact[0]["relation_scores"][0]
    assert manifest["compact_output"] is True
    assert score["edge_id"] == "e1"
    assert score["source"] == "g0"
    assert score["target"] == "g1"
    assert score["hint"] == "subscript_zone"
    assert "predicted_relation" in score
    assert "confidence" in score
    assert score["features"] == {"dx_over_height": 0.2, "dy_over_height": 0.6}
    assert "probabilities" not in score


def test_torch_stream_can_write_direct_structural_candidates_without_score_jsonl(tmp_path: Path) -> None:
    model, _report = train_edge_baseline([
        _edge_sample("e1", "subscript_zone", "SUB"),
        _edge_sample("e2", "right_neighbor", "HORIZONTAL"),
        _edge_sample("e3", "far_context", "NO_RELATION"),
    ], epochs=2)
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text(
        json.dumps(
            {
                "row_id": "r1",
                "coverage_tags": ["subscript"],
                "candidate_edges": [
                    {
                        "edge_id": "e1",
                        "source": "g0",
                        "target": "g1",
                        "hint": "subscript_zone",
                        "features": {"dx_over_height": 0.2, "dy_over_height": 0.6},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    from src.core.tinybdmath_structural_candidate import TinyBDStructuralCandidateStreamWriter

    structural_dir = tmp_path / "structural"
    with TinyBDStructuralCandidateStreamWriter(structural_dir, min_confidence=0.0) as writer:
        manifest = score_jsonl_stream_torch(
            rows_path,
            model,
            tmp_path / "scores",
            min_confidence=0.0,
            batch_rows=4,
            compact_output=True,
            scored_batch_callback=writer.write_scored_rows,
            write_scores=False,
        )
        structural_manifest = writer.close()

    assert manifest["score_jsonl_written"] is False
    assert not (tmp_path / "scores" / "tinybdmath_relation_scores.jsonl").exists()
    assert structural_manifest["rows"] == 1
    assert structural_manifest["source"] == "scored_rows_stream"
    structural_rows = [
        json.loads(line)
        for line in (structural_dir / "tinybdmath_structural_candidates.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert structural_rows[0]["row_id"] == "r1"
    assert structural_rows[0]["selected_relations"]


def _edge_sample(edge_id: str, hint: str, label: str) -> dict:
    return {
        "row_id": "r",
        "edge_id": edge_id,
        "hint": hint,
        "label": label,
        "features": {
            "dx_over_height": 0.4,
            "dy_over_height": 0.5 if hint == "subscript_zone" else 0.0,
            "x_overlap": 0.0,
            "y_overlap": 0.5,
            "size_ratio": 0.8,
            "same_font": 1,
        },
    }
