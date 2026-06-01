from pathlib import Path

from src.core.tinybdmath_edge_baseline import (
    EDGE_FEATURES,
    EDGE_LABELS,
    TinyBDEdgeBaselineModel,
    train_edge_baseline,
)
from src.core.tinybdmath_relation_scorer import TinyBDRelationScorer, score_rows


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
