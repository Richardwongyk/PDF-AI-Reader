from pathlib import Path

from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_FEATURES,
    GRAPH_PARSER_RELATIONS,
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
    graph_parser_predictions_to_structural_candidate,
    training_samples_from_rows,
)
from src.core.tinybdmath_alignment import TinyBDAlignmentBuilder
from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder


def test_graph_parser_training_samples_include_positive_and_none_edges() -> None:
    graph_row = _graph_row("r1", ["h", "t"])
    target = TinyBDTargetTreeBuilder().build_from_latex("h_t", row_id="r1").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])

    assert any(sample["relation"] == "SUB" for sample in samples)
    assert any(sample["relation"] == "NONE" for sample in samples)
    assert set(GRAPH_PARSER_FEATURES).issubset(samples[0]["features"])


def test_graph_parser_artifact_round_trips_and_predicts_candidate(tmp_path: Path) -> None:
    artifact = _toy_artifact()
    path = tmp_path / "model.json"
    artifact.save(path)
    parser = TinyBDGraphParser.load(path)

    payload = parser.predict_row(_graph_row("r1", ["h", "t"]), threshold=0.01)
    structural = graph_parser_predictions_to_structural_candidate(payload)

    assert payload["candidate_only"] is True
    assert payload["model_version"] == "toy"
    assert structural["candidate_only"] is True


def _toy_artifact() -> TinyBDGraphParserArtifact:
    relation_count = len(GRAPH_PARSER_RELATIONS)
    feature_count = len(GRAPH_PARSER_FEATURES)
    weights = []
    for relation in GRAPH_PARSER_RELATIONS:
        row = [0.0 for _ in range(feature_count)]
        if relation == "NEXT":
            row[GRAPH_PARSER_FEATURES.index("dx")] = 1.0
        weights.append(tuple(row))
    return TinyBDGraphParserArtifact(
        version="tinybdmath_graph_parser_m1_json_v1",
        model_version="toy",
        feature_version="tinybdmath_graph_parser_features_v1",
        feature_names=GRAPH_PARSER_FEATURES,
        relation_labels=GRAPH_PARSER_RELATIONS,
        means=tuple(0.0 for _ in range(feature_count)),
        scales=tuple(1.0 for _ in range(feature_count)),
        hidden_weights=(),
        hidden_biases=(),
        output_weights=tuple(weights),
        output_bias=tuple(0.0 for _ in range(relation_count)),
        threshold=0.01,
        train_config={"mode": "toy"},
    )


def _graph_row(row_id: str, texts: list[str]) -> dict:
    glyphs = []
    for index, text in enumerate(texts):
        glyphs.append(
            {
                "node_id": f"g{index:04d}",
                "node_type": "glyph",
                "text": text,
                "unicode": text,
                "latex": text,
                "font": "CMR",
                "bbox": [float(index * 10), 0.0, float(index * 10 + 5), 8.0],
                "is_math_font": True,
            }
        )
    return {
        "row_id": row_id,
        "input_hash": row_id + "-hash",
        "glyph_nodes": glyphs,
        "vector_nodes": [],
    }
