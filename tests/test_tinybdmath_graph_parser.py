from pathlib import Path

from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_FEATURES,
    GRAPH_PARSER_NODE_FEATURES,
    GRAPH_PARSER_NODE_LABELS,
    GRAPH_PARSER_RELATIONS,
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
    graph_parser_predictions_to_structural_candidate,
    node_training_samples_from_rows,
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


def test_graph_parser_node_samples_label_symbol_operator_and_spacing_nodes() -> None:
    graph_row = _graph_row("r1", [" ", "h", "+", "t"])
    target = TinyBDTargetTreeBuilder().build_from_latex("h+t", row_id="r1").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = node_training_samples_from_rows([graph_row], [alignment])
    by_node = {sample["node_id"]: sample for sample in samples}

    assert by_node["g0000"]["label"] == "SPACING"
    assert by_node["g0001"]["label"] == "SYMBOL"
    assert by_node["g0002"]["label"] == "OPERATOR"
    assert by_node["g0003"]["label"] == "SYMBOL"
    assert set(GRAPH_PARSER_NODE_FEATURES).issubset(by_node["g0001"]["features"])


def test_graph_parser_node_samples_label_text_run_nodes() -> None:
    graph_row = _graph_row("r2", ["d", "m", "o", "d", "e", "l"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"d_{\text{model}}", row_id="r2").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = node_training_samples_from_rows([graph_row], [alignment])
    labels = {sample["node_id"]: sample["label"] for sample in samples}

    assert labels["g0000"] == "SYMBOL"
    assert labels["g0001"] == "TEXT"
    assert labels["g0005"] == "TEXT"


def test_graph_parser_samples_label_horizontal_rule_node_and_fraction_relations() -> None:
    graph_row = _graph_row(
        "frac",
        ["x", "y"],
        glyph_bboxes=[
            [2.0, 0.0, 7.0, 6.0],
            [2.0, 12.0, 7.0, 18.0],
        ],
        vector_nodes=[{"node_id": "v0000", "bbox": [0.0, 8.0, 10.0, 8.5]}],
    )
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\frac{x}{y}", row_id="frac").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    node_samples = node_training_samples_from_rows([graph_row], [alignment])
    relation_samples = training_samples_from_rows([graph_row], [alignment])

    node_labels = {sample["node_id"]: sample["label"] for sample in node_samples}
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in relation_samples}
    assert node_labels["v0000"] == "HORIZONTAL_RULE"
    assert ("v0000", "v0000", "FRACTION_BAR") in relations
    assert ("v0000", "g0000", "ABOVE") in relations
    assert ("v0000", "g0001", "BELOW") in relations


def test_graph_parser_node_samples_label_generic_rule_vectors() -> None:
    graph_row = _graph_row(
        "rules",
        ["x"],
        glyph_bboxes=[
            [4.0, 4.0, 8.0, 10.0],
        ],
        vector_nodes=[
            {"node_id": "v0000", "bbox": [0.0, 1.0, 15.0, 1.4]},
            {"node_id": "v0001", "bbox": [0.0, 0.0, 0.4, 15.0]},
        ],
    )
    target = TinyBDTargetTreeBuilder().build_from_latex("x", row_id="rules").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = node_training_samples_from_rows([graph_row], [alignment])
    labels = {sample["node_id"]: sample["label"] for sample in samples}

    assert labels["v0000"] == "HORIZONTAL_RULE"
    assert labels["v0001"] == "VERTICAL_RULE"


def test_graph_parser_artifact_round_trips_and_predicts_candidate(tmp_path: Path) -> None:
    artifact = _toy_artifact()
    path = tmp_path / "model.json"
    artifact.save(path)
    parser = TinyBDGraphParser.load(path)

    payload = parser.predict_row(_graph_row("r1", ["h", "t"]), threshold=0.01)
    structural = graph_parser_predictions_to_structural_candidate(payload)

    assert payload["candidate_only"] is True
    assert payload["model_version"] == "toy"
    assert payload["node_predictions"]
    assert structural["candidate_only"] is True


def test_graph_parser_structural_candidate_accepts_model_structure_relations() -> None:
    payload = {
        "model_version": "toy",
        "node_predictions": [],
        "predictions": [
            {"source": "v0000", "target": "v0000", "relation": "FRACTION_BAR", "confidence": 0.97},
            {"source": "v0000", "target": "g0000", "relation": "ABOVE", "confidence": 0.93},
            {"source": "v0000", "target": "g0001", "relation": "BELOW", "confidence": 0.94},
        ],
    }
    structural = graph_parser_predictions_to_structural_candidate(payload)

    relations = {(item["source"], item["target"], item["relation"]) for item in structural["selected_relations"]}
    assert ("v0000", "v0000", "FRACTION_BAR") in relations
    assert ("v0000", "g0000", "ABOVE") in relations
    assert ("v0000", "g0001", "BELOW") in relations


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
        node_feature_names=GRAPH_PARSER_NODE_FEATURES,
        node_label_names=GRAPH_PARSER_NODE_LABELS,
        node_means=tuple(0.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_scales=tuple(1.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_hidden_weights=(),
        node_hidden_biases=(),
        node_output_weights=_toy_node_weights(),
        node_output_bias=tuple(0.0 for _ in GRAPH_PARSER_NODE_LABELS),
    )


def _toy_node_weights() -> tuple[tuple[float, ...], ...]:
    rows = []
    for label in GRAPH_PARSER_NODE_LABELS:
        row = [0.0 for _ in GRAPH_PARSER_NODE_FEATURES]
        if label in {"SYMBOL", "TEXT", "OPERATOR"}:
            row[GRAPH_PARSER_NODE_FEATURES.index("text_length")] = 1.0
        if label == "SPACING":
            row[GRAPH_PARSER_NODE_FEATURES.index("text_is_blank")] = 2.0
        if label == "HORIZONTAL_RULE":
            row[GRAPH_PARSER_NODE_FEATURES.index("is_rule")] = 8.0
        rows.append(tuple(row))
    return tuple(rows)


def _graph_row(
    row_id: str,
    texts: list[str],
    *,
    glyph_bboxes: list[list[float]] | None = None,
    vector_nodes: list[dict] | None = None,
) -> dict:
    glyphs = []
    for index, text in enumerate(texts):
        bbox = glyph_bboxes[index] if glyph_bboxes is not None else [float(index * 10), 0.0, float(index * 10 + 5), 8.0]
        glyphs.append(
            {
                "node_id": f"g{index:04d}",
                "node_type": "glyph",
                "text": text,
                "unicode": text,
                "latex": text,
                "font": "CMR",
                "bbox": bbox,
                "is_math_font": True,
            }
        )
    vectors = []
    for index, item in enumerate(vector_nodes or []):
        vectors.append(
            {
                "node_id": str(item.get("node_id", "") or f"v{index:04d}"),
                "node_type": "vector",
                "bbox": item.get("bbox", [0.0, 0.0, 0.0, 0.0]),
            }
        )
    return {
        "row_id": row_id,
        "input_hash": row_id + "-hash",
        "glyph_nodes": glyphs,
        "vector_nodes": vectors,
    }
