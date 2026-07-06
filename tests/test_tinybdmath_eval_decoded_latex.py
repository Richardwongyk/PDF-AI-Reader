import json
from pathlib import Path

import pytest

from tools.tinybdmath_eval_decoded_latex import (
    _build_report,
    _candidates_from_graph_parser,
    _candidates_from_graph_parser_torch,
    _decode_rows,
    _decode_rows_stream,
    _similarity,
)
from tests.test_tinybdmath_graph_parser import _graph_row, _toy_artifact, _toy_m5_artifact


def test_decoded_latex_eval_counts_identical_commands_as_exact() -> None:
    assert _similarity(r"\Omega", r"\Omega") == 1.0
    assert _similarity(r"\times", r"\times") == 1.0
    assert _similarity(r"\subseteq", r"\subseteq") == 1.0


def test_decoded_latex_stream_matches_batch(tmp_path: Path) -> None:
    graph_rows = [
        {
            "row_id": "r1",
            "kind": "inline",
            "label_latex": "x",
            "glyph_nodes": [{"node_id": "g0", "unicode": "x"}],
        },
        {
            "row_id": "r2",
            "kind": "inline",
            "label_latex": "y",
            "glyph_nodes": [{"node_id": "g0", "unicode": "y"}],
        },
    ]
    candidates = [
        {"row_id": "r1", "selected_relations": [], "verifier_warnings": []},
        {"row_id": "r2", "selected_relations": [], "verifier_warnings": []},
    ]
    graph_path = tmp_path / "graph.jsonl"
    candidates_path = tmp_path / "candidates.jsonl"
    graph_path.write_text("\n".join(json.dumps(row) for row in graph_rows) + "\n", encoding="utf-8")
    candidates_path.write_text("\n".join(json.dumps(row) for row in candidates) + "\n", encoding="utf-8")

    batch_rows, batch_warnings = _decode_rows(candidates, {row["row_id"]: row for row in graph_rows})
    stream_rows, stream_warnings = _decode_rows_stream(graph_path, candidates_path)

    assert stream_rows == batch_rows
    stream_report = _build_report(stream_rows, stream_warnings, streaming=True)
    batch_report = _build_report(batch_rows, batch_warnings, streaming=False)

    assert stream_report["metrics"] == batch_report["metrics"]
    assert stream_report["layout_gate"] == batch_report["layout_gate"]
    assert stream_report["layout_gate"]["status_counts"] == {"pass": 2}


def test_decoded_latex_eval_reports_n_best_oracle_without_replacing_rank_one() -> None:
    graph_row = {
        "row_id": "r1",
        "kind": "inline",
        "label_latex": "x^{i}",
        "glyph_nodes": [
            {"node_id": "g0", "latex": "x", "bbox": [0, 0, 8, 10]},
            {"node_id": "g1", "latex": "i", "bbox": [9, 4, 12, 10]},
        ],
    }
    candidate = {
        "row_id": "r1",
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
    }

    rows, warnings = _decode_rows([candidate], {"r1": graph_row})
    report = _build_report(rows, warnings, streaming=False)

    assert rows[0]["decoded_latex"] == "x_{i}"
    assert rows[0]["n_best_similarity"] == 1.0
    assert [item["latex"] for item in rows[0]["latex_candidates"]] == ["x_{i}", "x^{i}"]
    assert report["metrics"]["exact_match_rate"] == 0.0
    assert report["n_best_oracle_metrics"]["oracle_exact_match_rate"] == 1.0
    assert report["accepted_latex_emitted"] is False


def test_decoded_latex_eval_can_generate_candidates_from_graph_parser_model(tmp_path: Path) -> None:
    model_path = tmp_path / "graph_parser.json"
    _toy_artifact().save(model_path)
    graph_rows = [_graph_row("r1", ["h", "t"])]

    candidates = _candidates_from_graph_parser(graph_rows, model_path)
    rows, warnings = _decode_rows(candidates, {"r1": graph_rows[0]})

    assert warnings
    assert candidates[0]["row_id"] == "r1"
    assert candidates[0]["candidate_only"] is True
    assert rows[0]["decoded_latex"]
    assert rows[0]["manual_review_recommendation"]["accepted"] is False


def test_decoded_latex_eval_torch_inference_supports_m5_graph_context(tmp_path: Path) -> None:
    pytest.importorskip("torch")

    model_path = tmp_path / "graph_parser_m5.json"
    _toy_m5_artifact().save(model_path)
    graph_rows = [_graph_row("m5", ["l", "i", "m"])]

    candidates = _candidates_from_graph_parser_torch(graph_rows, model_path, batch_size=16)
    relations = {item["relation"] for item in candidates[0]["selected_relations"]}

    assert candidates[0]["model_version"] == "tinybdmath_graph_parser_m5"
    assert candidates[0]["keep_threshold"] == 0.5
    assert candidates[0]["graph_confidence"] > 0.0
    assert "TEXT_RUN_NEXT" in relations
