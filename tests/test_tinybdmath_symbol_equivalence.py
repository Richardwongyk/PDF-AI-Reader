from src.core.tinybdmath_symbol_equivalence import TinyBDSymbolEquivalence


def test_symbol_equivalence_uses_target_alias_and_pdf_identity() -> None:
    resolver = TinyBDSymbolEquivalence()

    assert resolver.equivalent(r"\subseteq", "⊆", "", ("⊆",))


def test_symbol_equivalence_does_not_invent_pdf_identity() -> None:
    resolver = TinyBDSymbolEquivalence()

    assert not resolver.equivalent(r"\subseteq", "", "not-subset")


def test_symbol_equivalence_uses_pdf_latex_identity_without_local_table() -> None:
    resolver = TinyBDSymbolEquivalence()

    assert resolver.equivalent("x", "", "x")
