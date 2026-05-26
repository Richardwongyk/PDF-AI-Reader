from src.core.tinybdmath_graph_baseline import graph_row_weak_label, train_graph_baseline


def test_graph_baseline_trains_and_scores_rows() -> None:
    rows = [
        _row("a", ["inline", "single_glyph_or_empty_text"], glyphs=1, edges=0),
        _row("b", ["inline", "subscript", "script_size_pdf_evidence"], glyphs=4, edges=8),
        _row("c", ["display", "fraction", "horizontal_rule"], glyphs=6, edges=20, vectors=1),
        _row("d", ["display", "matrix_or_array"], glyphs=20, edges=80),
        _row("e", ["inline", "math_alphabet"], glyphs=3, edges=5),
        _row("f", ["inline"], glyphs=5, edges=10),
    ]

    model, report = train_graph_baseline(rows, epochs=4)

    assert report["rows"] == 6
    assert model.predict_proba(rows[0])
    assert graph_row_weak_label(rows[2]) == "fraction_or_rule"


def _row(row_id: str, tags: list[str], *, glyphs: int, edges: int, vectors: int = 0) -> dict:
    return {
        "row_id": row_id,
        "case": "unit",
        "page_num": 1,
        "kind": "display" if "display" in tags else "inline",
        "coverage_tags": tags,
        "graph_stats": {
            "glyph_count": glyphs,
            "vector_count": vectors,
            "edge_count": edges,
            "math_font_glyphs": glyphs,
            "script_size_glyphs": 1 if "script_size_pdf_evidence" in tags else 0,
            "horizontal_rule_candidates": vectors,
            "font_count": 2,
            "edge_hint_counts": {
                "right_neighbor": max(edges - 2, 0),
                "subscript_zone": 1 if "subscript" in tags else 0,
                "superscript_zone": 1 if "superscript" in tags else 0,
                "far_context": 1,
            },
        },
    }
