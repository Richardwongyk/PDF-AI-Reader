import json
from pathlib import Path

from tools.tinybdmath_eval_decoded_latex import _build_report, _decode_rows, _decode_rows_stream, _similarity


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
    assert _build_report(stream_rows, stream_warnings, streaming=True)["metrics"] == _build_report(
        batch_rows,
        batch_warnings,
        streaming=False,
    )["metrics"]
