from src.core.latex_math_source_parser import extract_latex_math_spans
from tools.tinybdmath_instrumented_latex_dataset import (
    _box_blockers,
    _box_warnings,
    _capture_components,
    _color_each_display_row,
    _ensure_unique_marker_colors,
    _marker_color,
)


def test_inline_dollar_keeps_nested_text_math_inside_outer_span() -> None:
    text = r"define $T^k(V) = \underbrace{V \otimes V}_{\text{$k$ times}}$."

    spans = extract_latex_math_spans(text)

    assert len(spans) == 1
    assert spans[0].kind == "inline"
    assert spans[0].body == r"T^k(V) = \underbrace{V \otimes V}_{\text{$k$ times}}"


def test_skips_asymptote_source_language_math_strings() -> None:
    text = r"""
\begin{asy}
dot("$x$", (0, 0));
label("$U = f^{\text{pre}}(V)$", (1, 0));
\end{asy}
Visible text $y_1$.
\begin{asydef}
string s = "$z$";
\end{asydef}
"""

    spans = extract_latex_math_spans(text)

    assert [span.body for span in spans] == ["y_1"]


def test_stops_at_uncommented_endinput() -> None:
    text = r"Visible $x$. % \endinput ignored in comments" "\n" r"\endinput Hidden $y$."

    spans = extract_latex_math_spans(text)

    assert [span.body for span in spans] == ["x"]


def test_colors_each_alignment_cell_after_alignment_tabs() -> None:
    colored = _color_each_display_row(r"& x \in A \\ &\iff \text{$x$}", "A1B2C3")

    assert colored.count(r"\color[HTML]{A1B2C3}") == 2
    assert r"& \color[HTML]{A1B2C3} x \in A" in colored
    assert r"&\color[HTML]{A1B2C3} \iff \text{$x$}" in colored


def test_marker_colors_are_unique_for_large_napkin_scale() -> None:
    colors = [_marker_color(index) for index in range(40000)]

    assert len(set(colors)) == len(colors)


def test_marker_colors_stay_unique_when_cases_are_offset() -> None:
    attention = [_marker_color(index) for index in range(138)]
    napkin = [_marker_color(138 + index) for index in range(29743)]

    assert len(set(attention + napkin)) == 29881


def test_marker_color_collision_guard_rejects_duplicates() -> None:
    class Marker:
        def __init__(self, marker_id: str, color_int: int) -> None:
            self.marker_id = marker_id
            self.color_int = color_int

    try:
        _ensure_unique_marker_colors([Marker("a", 0x123456), Marker("b", 0x123456)])  # type: ignore[list-item]
    except RuntimeError as exc:
        assert "marker color collision" in str(exc)
    else:
        raise AssertionError("duplicate marker color was not rejected")


def test_inline_disconnected_capture_is_not_verified() -> None:
    glyphs = [
        {"text": "∼", "bbox": [337.072, 328.353, 345.559, 339.262]},
        {"text": "ℵ", "bbox": [253.008, 428.455, 259.673, 439.364]},
        {"text": "λ", "bbox": [259.675, 432.67, 264.616, 440.64]},
    ]
    components = _capture_components(glyphs, [])

    blockers = _box_blockers(
        type("Marker", (), {
            "kind": "inline",
            "source_offset_status": "exact_offset",
            "macro_expansion_warnings": (),
        })(),
        type("Capture", (), {"glyphs": glyphs, "vectors": []})(),
        [253.008, 328.353, 345.559, 440.64],
        components,
    )

    assert len(components) == 2
    assert "inline_disconnected_marker_capture" not in blockers
    assert "inline_disconnected_marker_capture" in _box_warnings(
        type("Marker", (), {"kind": "inline"})(),
        components,
        [11],
    )
