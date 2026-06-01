from src.core.tinybdmath_alignment import TinyBDAlignmentBuilder
from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder


def test_alignment_builds_hard_script_and_next_labels() -> None:
    graph_row = _graph_row("r1", ["h", "t", "-", "1"])
    target = TinyBDTargetTreeBuilder().build_from_latex("h_{t-1}", row_id="r1").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_node_alignments"] == 4
    assert result["stats"]["hard_alignment_rate"] == 1.0
    relations = {(item["source"], item["target"], item["relation"]) for item in result["relation_labels"]}
    assert ("g0000", "g0001", "SUB") in relations
    assert ("g0001", "g0002", "NEXT") in relations


def test_alignment_ignores_unmatched_pdf_artifacts_and_tracks_radical_body() -> None:
    graph_row = _graph_row("r2", [r"\sqrt", "d", "k", " "])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\sqrt{d_k}", row_id="r2").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_node_alignments"] >= 2
    assert any(item["relation"] == "RADICAL_BODY" for item in result["relation_labels"])
    assert result["ignored_pdf_nodes"]


def test_alignment_uses_parser_identity_for_tex_command_symbols() -> None:
    graph_row = _graph_row("r3", ["⊆"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"\subseteq", row_id="r3").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_alignment_rate"] == 1.0
    assert result["warnings"] == []


def test_alignment_splits_text_run_against_pdf_glyphs() -> None:
    graph_row = _graph_row("r4", ["d", "m", "o", "d", "e", "l"])
    target = TinyBDTargetTreeBuilder().build_from_latex(r"d_{\text{model}}", row_id="r4").to_json()

    result = TinyBDAlignmentBuilder().align_row(graph_row, target).to_json()

    assert result["stats"]["hard_alignment_rate"] == 1.0
    assert any(item["relation"] == "SUB" for item in result["relation_labels"])


def test_alignment_rows_manifest_counts_missing_targets() -> None:
    rows, manifest = TinyBDAlignmentBuilder().align_rows([_graph_row("missing", ["x"])], [])

    assert len(rows) == 1
    assert rows[0]["warnings"] == ["target_row_missing"]
    assert manifest["warnings"]["target_row_missing"] == 1


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
