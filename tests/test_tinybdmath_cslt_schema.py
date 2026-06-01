from src.core.tinybdmath_cslt_schema import CSLTBuilder, cslt_from_json


def test_cslt_schema_serializes_scripts_and_hashes_stably() -> None:
    builder = CSLTBuilder()
    script = builder.add_node("script")
    base = builder.add_node("symbol", value="h", latex="h")
    sub = builder.add_node("group")
    t = builder.add_node("symbol", value="t", latex="t")
    minus = builder.add_node("symbol", value="-", latex="-")
    one = builder.add_node("symbol", value="1", latex="1")
    builder.add_edge(script, base, "base", order=0)
    builder.add_edge(script, sub, "sub", order=1)
    builder.add_edge(sub, t, "child", order=0)
    builder.add_edge(sub, minus, "child", order=1)
    builder.add_edge(sub, one, "child", order=2)

    tree = builder.build(script)
    payload = tree.to_json()
    restored = cslt_from_json(payload)

    assert payload["schema_version"] == "tinybdmath_cslt_v1"
    assert tree.stable_hash() == restored.stable_hash()
    assert tree.to_latex() == "h_{t-1}"


def test_cslt_schema_represents_radical_text_fraction_matrix_and_artifact() -> None:
    builder = CSLTBuilder()
    root = builder.add_node("group")
    radical = builder.add_node("radical")
    script = builder.add_node("script")
    d = builder.add_node("symbol", value="d", latex="d")
    k = builder.add_node("symbol", value="k", latex="k")
    text = builder.add_node("text_run", value="model")
    fraction = builder.add_node("fraction")
    x = builder.add_node("symbol", value="x", latex="x")
    y = builder.add_node("symbol", value="y", latex="y")
    matrix = builder.add_node("matrix", attrs={"environment": "matrix"})
    row = builder.add_node("group", attrs={"role": "matrix_row"})
    cell = builder.add_node("group", attrs={"role": "matrix_cell"})
    artifact = builder.add_node("artifact", attrs={"reason": "spacing"})

    builder.add_edge(root, radical, "child", order=0)
    builder.add_edge(root, text, "child", order=1)
    builder.add_edge(root, fraction, "child", order=2)
    builder.add_edge(root, matrix, "child", order=3)
    builder.add_edge(root, artifact, "child", order=4)
    builder.add_edge(radical, script, "radical_body", order=0)
    builder.add_edge(script, d, "base", order=0)
    builder.add_edge(script, k, "sub", order=1)
    builder.add_edge(fraction, x, "numerator", order=0)
    builder.add_edge(fraction, y, "denominator", order=1)
    builder.add_edge(matrix, row, "matrix_row", order=0)
    builder.add_edge(row, cell, "matrix_cell", order=0)
    builder.add_edge(cell, x, "cell_content", order=0)

    tree = builder.build(root)

    assert r"\sqrt{d_{k}}" in tree.to_latex()
    assert r"\text{model}" in tree.to_latex()
    assert r"\frac{x}{y}" in tree.to_latex()
    assert any(node.node_type == "artifact" for node in tree.nodes)
