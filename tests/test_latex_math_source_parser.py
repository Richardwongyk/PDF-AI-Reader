from src.core.latex_math_source_parser import extract_latex_math_spans


def test_inline_dollar_keeps_nested_text_math_inside_outer_span() -> None:
    text = r"define $T^k(V) = \underbrace{V \otimes V}_{\text{$k$ times}}$."

    spans = extract_latex_math_spans(text)

    assert len(spans) == 1
    assert spans[0].kind == "inline"
    assert spans[0].body == r"T^k(V) = \underbrace{V \otimes V}_{\text{$k$ times}}"
