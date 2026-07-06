from pathlib import Path

from src.core.tinybdmath_graph_parser import (
    GRAPH_PARSER_FEATURES,
    GRAPH_PARSER_GRAPH_FEATURES,
    GRAPH_PARSER_NODE_FEATURES,
    GRAPH_PARSER_NODE_LABELS,
    GRAPH_PARSER_RELATIONS,
    TinyBDGraphParser,
    TinyBDGraphParserArtifact,
    graph_nodes,
    graph_parser_node_features,
    graph_parser_predictions_to_structural_candidate,
    graph_parser_structured_relation_selection,
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
    assert not any(sample["relation"] in {"BASE", "CHILD"} for sample in samples)
    assert set(GRAPH_PARSER_FEATURES).issubset(samples[0]["features"])
    assert set(GRAPH_PARSER_GRAPH_FEATURES).issubset(samples[0]["graph_features"])


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


def test_graph_parser_node_samples_label_operator_text_run_nodes() -> None:
    graph_row = _graph_row("operator", ["f", "o", "o", "(", "x", ")"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\operatorname{foo}(x)", row_id="operator").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = node_training_samples_from_rows([graph_row], [alignment])
    labels = {sample["node_id"]: sample["label"] for sample in samples}

    assert labels["g0000"] == "OPERATOR"
    assert labels["g0001"] == "OPERATOR"
    assert labels["g0002"] == "OPERATOR"


def test_graph_parser_node_samples_label_named_operator_text_run_nodes() -> None:
    graph_row = _graph_row("named-operator", ["l", "i", "m", "x"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\lim_x", row_id="named-operator", display_mode=True).to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = node_training_samples_from_rows([graph_row], [alignment])
    labels = {sample["node_id"]: sample["label"] for sample in samples}

    assert labels["g0000"] == "OPERATOR"
    assert labels["g0001"] == "OPERATOR"
    assert labels["g0002"] == "OPERATOR"
    assert labels["g0003"] == "SYMBOL"


def test_graph_parser_node_features_include_local_text_run_evidence() -> None:
    graph_row = _graph_row("run", ["l", "i", "m", "x"])
    nodes = graph_nodes(graph_row, include_blank=True)

    features = graph_parser_node_features(nodes[1], nodes)

    assert features["letter_run_left"] == 1.0
    assert features["letter_run_right"] == 2.0
    assert features["letter_run_length"] == 4.0
    assert features["same_font_letter_run_length"] == 4.0
    assert features["prev_baseline_alignment"] == 1.0


def test_graph_parser_training_samples_include_text_run_group_edges() -> None:
    graph_row = _graph_row("text", ["d", "m", "o", "d", "e", "l"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"d_{\text{model}}", row_id="text").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0001", "g0002", "TEXT_RUN_NEXT") in relations
    assert ("g0004", "g0005", "TEXT_RUN_NEXT") in relations


def test_graph_parser_relation_samples_carry_source_target_node_context() -> None:
    graph_row = _graph_row("context", ["l", "i", "m", "x"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\lim_x", row_id="context", display_mode=True).to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    text_run_sample = next(sample for sample in samples if sample["relation"] == "TEXT_RUN_NEXT")

    assert set(GRAPH_PARSER_NODE_FEATURES).issubset(text_run_sample["source_node_features"])
    assert set(GRAPH_PARSER_NODE_FEATURES).issubset(text_run_sample["target_node_features"])
    assert text_run_sample["source_node_features"]["letter_run_length"] >= 3.0


def test_graph_parser_training_samples_expand_script_group_members() -> None:
    graph_row = _graph_row("script-group", ["h", "t", "-", "1"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"h_{t-1}", row_id="script-group").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0000", "g0001", "SUB") in relations
    assert ("g0000", "g0002", "SUB") in relations
    assert ("g0000", "g0003", "SUB") in relations


def test_graph_parser_training_samples_consume_fraction_group_boundaries() -> None:
    graph_row = _graph_row("fraction-group", ["x", "y", "z", "w"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\frac{xy}{zw}", row_id="fraction-group").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0000", "g0001", "NEXT") in relations
    assert ("g0002", "g0003", "NEXT") in relations


def test_graph_parser_training_samples_include_radical_index_edges() -> None:
    graph_row = _graph_row("radical-index", [r"\sqrt", "3", "x"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt[3]{x}", row_id="radical-index").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0000", "g0001", "RADICAL_INDEX") in relations
    assert ("g0000", "g0002", "RADICAL_BODY") in relations


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


def test_graph_parser_training_samples_keep_under_over_relation_labels() -> None:
    graph_row = _graph_row("limits", [r"\sum", "i", "=", "1", "n"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\sum_{i=1}^{n}", row_id="limits", display_mode=True).to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {sample["relation"] for sample in samples}

    assert "UNDER" in relations
    assert "OVER" in relations


def test_graph_parser_training_samples_keep_left_attachment_relation_labels() -> None:
    graph_row = _graph_row("left-script", ["a", "b", "X"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"{}^a_b X", row_id="left-script", display_mode=True).to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0002", "g0000", "PRE_SUP") in relations
    assert ("g0002", "g0001", "PRE_SUB") in relations


def test_graph_parser_samples_label_overline_rule_from_accent_structure() -> None:
    graph_row = _graph_row(
        "accent",
        ["x"],
        glyph_bboxes=[
            [2.0, 4.0, 7.0, 10.0],
        ],
        vector_nodes=[{"node_id": "v0000", "bbox": [0.0, 1.0, 10.0, 1.4]}],
    )
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\overline{x}", row_id="accent").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("v0000", "v0000", "OVERLINE") in relations
    assert ("v0000", "g0000", "BELOW") in relations


def test_graph_parser_training_samples_include_fence_structure_edges() -> None:
    graph_row = _graph_row("fence", ["(", "x", ")"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\left(x\right)", row_id="fence").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0001", "g0000", "FENCE_OPEN") in relations
    assert ("g0001", "g0002", "FENCE_CLOSE") in relations
    assert ("g0000", "g0001", "FENCE_BODY") in relations
    assert ("g0002", "g0001", "FENCE_BODY") in relations


def test_graph_parser_training_samples_include_matrix_row_and_cell_edges() -> None:
    graph_row = _graph_row("matrix", ["x", "y", "z", "w"])
    target = TinyBDTargetTreeBuilder().build_from_latex(
        r"\begin{matrix}x&y\\z&w\end{matrix}",
        row_id="matrix",
        display_mode=True,
    ).to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("g0000", "g0002", "MATRIX_ROW") in relations
    assert ("g0000", "g0001", "MATRIX_CELL") in relations
    assert ("g0002", "g0003", "MATRIX_CELL") in relations


def test_graph_parser_training_samples_include_enclosure_body_edges() -> None:
    graph_row = _graph_row(
        "box",
        ["x"],
        glyph_bboxes=[
            [4.0, 4.0, 8.0, 10.0],
        ],
        vector_nodes=[{"node_id": "v0000", "bbox": [0.0, 1.0, 15.0, 1.4]}],
    )
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\boxed{x}", row_id="box").to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    samples = training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in samples}

    assert ("v0000", "g0000", "ENCLOSURE_BODY") in relations


def test_graph_parser_training_samples_include_equation_tag_edges_and_node_label() -> None:
    graph_row = _graph_row(
        "tag",
        ["x", "(1)"],
        glyph_bboxes=[
            [0.0, 0.0, 5.0, 8.0],
            [40.0, 0.0, 55.0, 8.0],
        ],
    )
    target = TinyBDTargetTreeBuilder().build_from_latex(
        r"\begin{equation}x\tag{1}\end{equation}",
        row_id="tag",
        display_mode=True,
    ).to_json()
    alignment = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    relation_samples = training_samples_from_rows([graph_row], [alignment])
    node_samples = node_training_samples_from_rows([graph_row], [alignment])
    relations = {(sample["source"], sample["target"], sample["relation"]) for sample in relation_samples}
    node_labels = {sample["node_id"]: sample["label"] for sample in node_samples}

    assert ("g0000", "g0001", "EQUATION_TAG") in relations
    assert node_labels["g0001"] == "EQUATION_TAG"


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


def test_graph_parser_m2_artifact_uses_node_context_relation_head(tmp_path: Path) -> None:
    artifact = _toy_m2_artifact()
    path = tmp_path / "m2-model.json"
    artifact.save(path)
    parser = TinyBDGraphParser.load(path)

    payload = parser.predict_row(_graph_row("m2", ["l", "i", "m"]), threshold=0.01)
    relations = {item["relation"] for item in payload["predictions"]}

    assert payload["model_version"] == "tinybdmath_graph_parser_m2"
    assert "TEXT_RUN_NEXT" in relations


def test_graph_parser_m3_artifact_uses_interaction_relation_head(tmp_path: Path) -> None:
    artifact = _toy_m3_artifact()
    path = tmp_path / "m3-model.json"
    artifact.save(path)
    parser = TinyBDGraphParser.load(path)

    payload = parser.predict_row(_graph_row("m3", ["l", "i", "m"]), threshold=0.01)
    relations = {item["relation"] for item in payload["predictions"]}

    assert payload["model_version"] == "tinybdmath_graph_parser_m3"
    assert "TEXT_RUN_NEXT" in relations


def test_graph_parser_m4_artifact_uses_keep_and_type_heads(tmp_path: Path) -> None:
    artifact = _toy_m4_artifact()
    path = tmp_path / "m4-model.json"
    artifact.save(path)
    parser = TinyBDGraphParser.load(path)

    payload = parser.predict_row(_graph_row("m4", ["l", "i", "m"]), threshold=0.8)
    relations = {item["relation"] for item in payload["predictions"]}

    assert payload["model_version"] == "tinybdmath_graph_parser_m4"
    assert payload["keep_threshold"] == 0.5
    assert payload["graph_confidence"] > 0.0
    assert "TEXT_RUN_NEXT" in relations


def test_graph_parser_m5_artifact_uses_whole_formula_graph_context(tmp_path: Path) -> None:
    artifact = _toy_m5_artifact()
    path = tmp_path / "m5-model.json"
    artifact.save(path)
    parser = TinyBDGraphParser.load(path)

    payload = parser.predict_row(_graph_row("m5", ["l", "i", "m"]), threshold=0.8)
    relations = {item["relation"] for item in payload["predictions"]}

    assert payload["model_version"] == "tinybdmath_graph_parser_m5"
    assert payload["keep_threshold"] == 0.5
    assert payload["graph_confidence"] > 0.0
    assert "TEXT_RUN_NEXT" in relations


def test_graph_parser_structural_candidate_accepts_model_structure_relations() -> None:
    payload = {
        "model_version": "toy",
        "node_predictions": [],
        "predictions": [
            {"source": "v0000", "target": "v0000", "relation": "FRACTION_BAR", "confidence": 0.97},
            {"source": "v0000", "target": "g0000", "relation": "ABOVE", "confidence": 0.93},
            {"source": "v0000", "target": "g0001", "relation": "BELOW", "confidence": 0.94},
            {"source": "g0002", "target": "g0003", "relation": "UNDER", "confidence": 0.92},
            {"source": "g0004", "target": "g0005", "relation": "TEXT_RUN_NEXT", "confidence": 0.90},
            {"source": "g0008", "target": "g0006", "relation": "PRE_SUP", "confidence": 0.88},
            {"source": "g0009", "target": "g0010", "relation": "RADICAL_INDEX", "confidence": 0.87},
            {"source": "v0011", "target": "g0011", "relation": "ENCLOSURE_BODY", "confidence": 0.86},
            {"source": "g0012", "target": "g0013", "relation": "EQUATION_TAG", "confidence": 0.85},
        ],
        "relation_alternatives": [
            {
                "source": "g0002",
                "target": "g0003",
                "alternatives": [
                    {"relation": "UNDER", "confidence": 0.92},
                    {"relation": "OVER", "confidence": 0.41},
                ],
            }
        ],
    }
    structural = graph_parser_predictions_to_structural_candidate(payload)

    relations = {(item["source"], item["target"], item["relation"]) for item in structural["selected_relations"]}
    assert ("v0000", "v0000", "FRACTION_BAR") in relations
    assert ("v0000", "g0000", "ABOVE") in relations
    assert ("g0002", "g0003", "UNDER") in relations
    assert ("g0004", "g0005", "TEXT_RUN_NEXT") in relations
    assert ("g0008", "g0006", "PRE_SUP") in relations
    assert ("g0009", "g0010", "RADICAL_INDEX") in relations
    assert ("v0011", "g0011", "ENCLOSURE_BODY") in relations
    assert ("g0012", "g0013", "EQUATION_TAG") in relations
    assert ("v0000", "g0001", "BELOW") in relations
    assert structural["relation_alternatives"][0]["alternatives"][0]["relation"] == "UNDER"
    assert structural["relation_alternatives"][0]["alternatives"][1]["relation"] == "OVER"


def test_graph_parser_structured_relation_selection_removes_conflicting_edges() -> None:
    selected = graph_parser_structured_relation_selection(
        [
            {"source": "a", "target": "b", "relation": "NEXT", "confidence": 0.70},
            {"source": "a", "target": "c", "relation": "NEXT", "confidence": 0.91},
            {"source": "x", "target": "s", "relation": "SUB", "confidence": 0.60},
            {"source": "y", "target": "s", "relation": "SUP", "confidence": 0.95},
            {"source": "rule", "target": "n1", "relation": "ABOVE", "confidence": 0.88},
            {"source": "rule", "target": "n2", "relation": "ABOVE", "confidence": 0.87},
        ]
    )
    relations = {(item["source"], item["target"], item["relation"]) for item in selected}

    assert ("a", "c", "NEXT") in relations
    assert ("a", "b", "NEXT") not in relations
    assert ("y", "s", "SUP") in relations
    assert ("x", "s", "SUB") not in relations
    assert ("rule", "n1", "ABOVE") in relations
    assert ("rule", "n2", "ABOVE") in relations


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


def _toy_m2_artifact() -> TinyBDGraphParserArtifact:
    relation_count = len(GRAPH_PARSER_RELATIONS)
    feature_count = len(GRAPH_PARSER_FEATURES)
    node_feature_count = len(GRAPH_PARSER_NODE_FEATURES)
    node_label_count = len(GRAPH_PARSER_NODE_LABELS)
    context_width = feature_count + (2 * node_feature_count) + (2 * node_label_count)
    weights = []
    source_node_offset = feature_count
    letter_run_index = source_node_offset + GRAPH_PARSER_NODE_FEATURES.index("letter_run_length")
    for relation in GRAPH_PARSER_RELATIONS:
        row = [0.0 for _ in range(context_width)]
        if relation == "TEXT_RUN_NEXT":
            row[letter_run_index] = 4.0
        weights.append(tuple(row))
    return TinyBDGraphParserArtifact(
        version="tinybdmath_graph_parser_m2_json_v1",
        model_version="tinybdmath_graph_parser_m2",
        feature_version="tinybdmath_graph_parser_features_v6",
        feature_names=GRAPH_PARSER_FEATURES,
        relation_labels=GRAPH_PARSER_RELATIONS,
        means=tuple(0.0 for _ in range(feature_count)),
        scales=tuple(1.0 for _ in range(feature_count)),
        hidden_weights=(),
        hidden_biases=(),
        output_weights=tuple(weights),
        output_bias=tuple(0.0 for _ in range(relation_count)),
        threshold=0.01,
        train_config={"mode": "graph_parser_m2"},
        node_feature_names=GRAPH_PARSER_NODE_FEATURES,
        node_label_names=GRAPH_PARSER_NODE_LABELS,
        node_means=tuple(0.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_scales=tuple(1.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_hidden_weights=(),
        node_hidden_biases=(),
        node_output_weights=_toy_node_weights(),
        node_output_bias=tuple(0.0 for _ in GRAPH_PARSER_NODE_LABELS),
    )


def _toy_m3_artifact() -> TinyBDGraphParserArtifact:
    relation_count = len(GRAPH_PARSER_RELATIONS)
    node_label_count = len(GRAPH_PARSER_NODE_LABELS)
    edge_width = len(GRAPH_PARSER_FEATURES)
    node_width = len(GRAPH_PARSER_NODE_FEATURES)
    fusion_width = edge_width + (4 * node_width) + (4 * node_label_count)
    source_offset = edge_width
    target_offset = edge_width + node_width
    abs_delta_offset = edge_width + (2 * node_width)
    weights = []
    for relation in GRAPH_PARSER_RELATIONS:
        row = [0.0 for _ in range(fusion_width)]
        if relation == "TEXT_RUN_NEXT":
            row[source_offset + GRAPH_PARSER_NODE_FEATURES.index("letter_run_length")] = 2.0
            row[target_offset + GRAPH_PARSER_NODE_FEATURES.index("letter_run_length")] = 2.0
            row[abs_delta_offset + GRAPH_PARSER_NODE_FEATURES.index("prev_baseline_alignment")] = -1.0
        weights.append(tuple(row))
    return TinyBDGraphParserArtifact(
        version="tinybdmath_graph_parser_m3_json_v1",
        model_version="tinybdmath_graph_parser_m3",
        feature_version="tinybdmath_graph_parser_features_v7",
        feature_names=GRAPH_PARSER_FEATURES,
        relation_labels=GRAPH_PARSER_RELATIONS,
        means=tuple(0.0 for _ in range(len(GRAPH_PARSER_FEATURES))),
        scales=tuple(1.0 for _ in range(len(GRAPH_PARSER_FEATURES))),
        hidden_weights=(),
        hidden_biases=(),
        output_weights=tuple(weights),
        output_bias=tuple(0.0 for _ in range(relation_count)),
        threshold=0.01,
        train_config={"mode": "graph_parser_m3"},
        node_feature_names=GRAPH_PARSER_NODE_FEATURES,
        node_label_names=GRAPH_PARSER_NODE_LABELS,
        node_means=tuple(0.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_scales=tuple(1.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_hidden_weights=(),
        node_hidden_biases=(),
        node_output_weights=_toy_node_weights(),
        node_output_bias=tuple(0.0 for _ in GRAPH_PARSER_NODE_LABELS),
    )


def _toy_m4_artifact() -> TinyBDGraphParserArtifact:
    relation_count = len(GRAPH_PARSER_RELATIONS)
    node_label_count = len(GRAPH_PARSER_NODE_LABELS)
    edge_width = len(GRAPH_PARSER_FEATURES)
    node_width = len(GRAPH_PARSER_NODE_FEATURES)
    fusion_width = edge_width + (4 * node_width) + (4 * node_label_count)
    none_index = GRAPH_PARSER_RELATIONS.index("NONE")
    text_run_index = GRAPH_PARSER_RELATIONS.index("TEXT_RUN_NEXT")
    output_weights = [[0.0 for _ in range(fusion_width)] for _ in range(relation_count)]
    output_bias = [0.0 for _ in range(relation_count)]
    output_bias[none_index] = 5.0
    output_bias[text_run_index] = 4.0
    for index in range(relation_count):
        if index not in {none_index, text_run_index}:
            output_bias[index] = -4.0
    keep_weights = [0.0 for _ in range(fusion_width)]
    source_offset = edge_width
    target_offset = edge_width + node_width
    keep_weights[source_offset + GRAPH_PARSER_NODE_FEATURES.index("letter_run_length")] = 2.5
    keep_weights[target_offset + GRAPH_PARSER_NODE_FEATURES.index("letter_run_length")] = 2.5
    return TinyBDGraphParserArtifact(
        version="tinybdmath_graph_parser_m4_json_v1",
        model_version="tinybdmath_graph_parser_m4",
        feature_version="tinybdmath_graph_parser_features_v8",
        feature_names=GRAPH_PARSER_FEATURES,
        relation_labels=GRAPH_PARSER_RELATIONS,
        means=tuple(0.0 for _ in range(len(GRAPH_PARSER_FEATURES))),
        scales=tuple(1.0 for _ in range(len(GRAPH_PARSER_FEATURES))),
        hidden_weights=(),
        hidden_biases=(),
        output_weights=tuple(tuple(row) for row in output_weights),
        output_bias=tuple(output_bias),
        threshold=0.8,
        train_config={"mode": "graph_parser_m4"},
        node_feature_names=GRAPH_PARSER_NODE_FEATURES,
        node_label_names=GRAPH_PARSER_NODE_LABELS,
        node_means=tuple(0.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_scales=tuple(1.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_hidden_weights=(),
        node_hidden_biases=(),
        node_output_weights=_toy_node_weights(),
        node_output_bias=tuple(0.0 for _ in GRAPH_PARSER_NODE_LABELS),
        keep_output_weights=tuple(keep_weights),
        keep_output_bias=0.0,
        keep_threshold=0.5,
    )


def _toy_m5_artifact() -> TinyBDGraphParserArtifact:
    relation_count = len(GRAPH_PARSER_RELATIONS)
    node_label_count = len(GRAPH_PARSER_NODE_LABELS)
    edge_width = len(GRAPH_PARSER_FEATURES)
    node_width = len(GRAPH_PARSER_NODE_FEATURES)
    graph_width = len(GRAPH_PARSER_GRAPH_FEATURES)
    fusion_width = edge_width + (4 * node_width) + graph_width + (4 * node_label_count)
    none_index = GRAPH_PARSER_RELATIONS.index("NONE")
    text_run_index = GRAPH_PARSER_RELATIONS.index("TEXT_RUN_NEXT")
    output_weights = [[0.0 for _ in range(fusion_width)] for _ in range(relation_count)]
    output_bias = [0.0 for _ in range(relation_count)]
    output_bias[none_index] = 5.0
    output_bias[text_run_index] = 4.0
    for index in range(relation_count):
        if index not in {none_index, text_run_index}:
            output_bias[index] = -4.0
    keep_weights = [0.0 for _ in range(fusion_width)]
    graph_offset = edge_width + (2 * node_width)
    keep_weights[graph_offset + GRAPH_PARSER_GRAPH_FEATURES.index("node_count")] = 2.0
    return TinyBDGraphParserArtifact(
        version="tinybdmath_graph_parser_m5_json_v1",
        model_version="tinybdmath_graph_parser_m5",
        feature_version="tinybdmath_graph_parser_features_v9",
        feature_names=GRAPH_PARSER_FEATURES,
        relation_labels=GRAPH_PARSER_RELATIONS,
        means=tuple(0.0 for _ in range(len(GRAPH_PARSER_FEATURES))),
        scales=tuple(1.0 for _ in range(len(GRAPH_PARSER_FEATURES))),
        hidden_weights=(),
        hidden_biases=(),
        output_weights=tuple(tuple(row) for row in output_weights),
        output_bias=tuple(output_bias),
        threshold=0.8,
        train_config={"mode": "graph_parser_m5"},
        node_feature_names=GRAPH_PARSER_NODE_FEATURES,
        node_label_names=GRAPH_PARSER_NODE_LABELS,
        node_means=tuple(0.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_scales=tuple(1.0 for _ in GRAPH_PARSER_NODE_FEATURES),
        node_hidden_weights=(),
        node_hidden_biases=(),
        node_output_weights=_toy_node_weights(),
        node_output_bias=tuple(0.0 for _ in GRAPH_PARSER_NODE_LABELS),
        keep_output_weights=tuple(keep_weights),
        keep_output_bias=0.0,
        keep_threshold=0.5,
        graph_feature_names=GRAPH_PARSER_GRAPH_FEATURES,
        graph_means=tuple(0.0 for _ in GRAPH_PARSER_GRAPH_FEATURES),
        graph_scales=tuple(1.0 for _ in GRAPH_PARSER_GRAPH_FEATURES),
        graph_hidden_weights=(),
        graph_hidden_biases=(),
    )


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
