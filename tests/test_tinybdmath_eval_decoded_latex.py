from tools.tinybdmath_eval_decoded_latex import _similarity


def test_decoded_latex_eval_counts_identical_commands_as_exact() -> None:
    assert _similarity(r"\Omega", r"\Omega") == 1.0
    assert _similarity(r"\times", r"\times") == 1.0
    assert _similarity(r"\subseteq", r"\subseteq") == 1.0
