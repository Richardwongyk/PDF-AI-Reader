from src.core.latex_math_source_parser import extract_latex_math_spans
from tools.tinybdmath_instrumented_latex_dataset import _color_each_display_row


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
