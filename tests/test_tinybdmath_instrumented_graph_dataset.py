import json
from pathlib import Path

from tools.tinybdmath_instrumented_latex_dataset import CAPTURE_QUALITY_VERSION
from tools.tinybdmath_instrumented_graph_dataset import build_graph_dataset, write_graph_dataset


def test_builds_graph_rows_manifest_and_page_grouped_split(tmp_path: Path) -> None:
    source = tmp_path / "instrumented_training_rows.jsonl"
    source.write_text(
        "\n".join(
            [
                _row("a", "inline", "h_{t-1}", 1),
                _row("b", "display", r"\\frac{x}{y}", 1, vector=True),
                _row("c", "inline", "", 2),
            ]
        ),
        encoding="utf-8",
    )

    result = build_graph_dataset([source], split_seed="unit-test")

    assert result.manifest["rows"] == 2
    assert result.manifest["blockers"]["missing_label_latex"] == 1
    assert result.manifest["coverage_tags"]["subscript"] == 1
    assert result.manifest["coverage_tags"]["fraction"] == 1
    assert result.manifest["coverage_tags"]["horizontal_rule"] == 1
    assert result.rows[0]["glyph_nodes"][1]["is_script_size"] is True
    assert any(edge["hint"] == "subscript_zone" for edge in result.rows[0]["candidate_edges"])
    assert sum(result.split["row_counts"].values()) == 2

    write_graph_dataset(result, tmp_path / "out")
    assert (tmp_path / "out" / "tinybdmath_graph_rows.jsonl").exists()
    assert (tmp_path / "out" / "tinybdmath_graph_manifest.json").exists()
    assert (tmp_path / "out" / "tinybdmath_graph_split.json").exists()


def test_graph_dataset_rejects_legacy_unverified_capture_quality(tmp_path: Path) -> None:
    source = tmp_path / "instrumented_training_rows.jsonl"
    legacy = json.loads(_row("legacy", "inline", "x", 1))
    legacy.pop("capture_quality_version", None)
    source.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    result = build_graph_dataset([source], split_seed="unit-test")

    assert result.manifest["rows"] == 0
    assert result.manifest["blockers"]["capture_quality_not_verified"] == 1


def test_graph_dataset_does_not_build_edges_across_pages(tmp_path: Path) -> None:
    source = tmp_path / "instrumented_training_rows.jsonl"
    row = json.loads(_row("cross", "display", r"x+y", 1))
    row["capture_pages"] = [1, 2]
    row["page_bboxes"] = [
        {"page_num": 1, "bbox": [8, 8, 18, 26]},
        {"page_num": 2, "bbox": [100, 8, 120, 26]},
    ]
    row["sampled_glyphs"] = [
        {"text": "x", "font": "CMMI10", "size": 10.0, "page_num": 1, "bbox": [10, 10, 16, 20]},
        {"text": "y", "font": "CMMI10", "size": 10.0, "page_num": 2, "bbox": [12, 10, 18, 20]},
    ]
    row["glyph_count"] = 2
    row["capture_component_count"] = 2
    source.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

    result = build_graph_dataset([source], split_seed="unit-test")

    assert result.manifest["rows"] == 1
    assert result.rows[0]["capture_pages"] == [1, 2]
    assert result.rows[0]["page_bboxes"] == row["page_bboxes"]
    assert result.rows[0]["candidate_edges"] == []


def _row(source_id: str, kind: str, label: str, page: int, *, vector: bool = False) -> str:
    glyphs = [
        {"text": "h", "font": "CMMI10", "size": 10.0, "page_num": page, "bbox": [10, 10, 16, 20]},
        {"text": "t", "font": "CMMI7", "size": 7.0, "page_num": page, "bbox": [16, 14, 19, 21]},
        {"text": "-", "font": "CMSY7", "size": 7.0, "page_num": page, "bbox": [19, 14, 24, 21]},
        {"text": "1", "font": "CMR7", "size": 7.0, "page_num": page, "bbox": [24, 14, 28, 21]},
    ]
    vectors = [{"type": "line", "page_num": page, "bbox": [8, 25, 30, 25.5]}] if vector else []
    return json.dumps(
        {
            "schema_version": "tinybdmath_instrumented_training_row_v1",
            "case": "unit",
            "source_id": source_id,
            "marker_id": f"m_{source_id}",
            "kind": kind,
            "label_latex": label,
            "raw_source_latex": label,
            "label_source": "latex_source_macro_expanded",
            "pdf_window_source": "instrumented_latex_color",
            "capture_quality_version": CAPTURE_QUALITY_VERSION,
            "page_num": page,
            "capture_pages": [page],
            "page_bboxes": [{"page_num": page, "bbox": [8, 8, 32, 26]}],
            "bbox": [8, 8, 32, 26],
            "tex_path": "main.tex",
            "capture_component_count": 1,
            "capture_components": [{"bbox": [8, 8, 32, 26], "glyph_count": len(glyphs), "vector_count": len(vectors), "text_sample": "ht-1"}],
            "sampled_glyphs": glyphs,
            "sampled_vectors": vectors,
            "verified_exact_box": True,
            "blockers": [],
            "warnings": [],
        },
        ensure_ascii=False,
    )
