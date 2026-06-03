import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MAINLINE_FILES = (
    "src/core/latex_mathml_extractor.py",
    "src/core/tinybdmath_target_tree.py",
    "src/core/tinybdmath_alignment.py",
    "src/core/tinybdmath_constrained_decode.py",
    "src/core/tinybdmath_graph_parser.py",
    "src/core/tinybdmath_latex_decoder.py",
    "src/core/tinybdmath_layout_verifier.py",
    "tools/tinybdmath_audit_structure_scope.py",
)

TEST_FILES = (
    "tests/test_latex_mathml_extractor.py",
    "tests/test_tinybdmath_target_tree.py",
    "tests/test_tinybdmath_alignment.py",
    "tests/test_tinybdmath_structure_scope_audit.py",
)

FORBIDDEN_LITERAL_FRAGMENTS = (
    "trainingMacros",
    "macros:",
    '"macros"',
    "'macros'",
    "\\\\prescript",
    "\\\\sideset",
    "script_side",
    "softmax",
    "argmax",
    "Attention",
    "LayerNorm",
    "MultiHead",
)

FORBIDDEN_PYTHON_IMPORTS = {"re"}
FORBIDDEN_CALL_NAMES = {"compile", "match", "search", "fullmatch", "sub", "subn"}
ALLOWED_COMMAND_CONSTANTS = {
    r"\begin{matrix}",
    r"\end{matrix}",
    r"\frac{",
    r"\sqrt",
    r"\hat",
    r"\overline{",
    r"\overbrace",
    r"\underline{",
    r"\underbrace",
}


def test_tinybdmath_parser_does_not_inject_latex_macros_or_sample_terms() -> None:
    offenders: list[str] = []
    for relative_path in MAINLINE_FILES + TEST_FILES:
        offenders.extend(_literal_offenders(relative_path, _read(relative_path)))

    assert offenders == []


def test_tinybdmath_mainline_does_not_use_regex_or_latex_command_branching() -> None:
    offenders: list[str] = []
    for relative_path in MAINLINE_FILES:
        offenders.extend(_structural_offenders(relative_path, _read(relative_path)))

    assert offenders == []


def test_tinybdmath_hardcoding_guard_catches_multiple_hardcoding_shapes() -> None:
    source = r'''
import re
MACROS = {"\\prescript": "{}^{#1}_{#2}{#3}"}
SAMPLES = ("softmax",)
def f(text):
    if text == "\\sideset":
        return "x"
    return re.sub("Attention", "", text)
'''

    offenders = _literal_offenders("synthetic.py", source)
    offenders.extend(_structural_offenders("synthetic.py", source))

    assert any("literal \\\\prescript" in item for item in offenders)
    assert any("literal softmax" in item for item in offenders)
    assert any("imports regex module" in item for item in offenders)
    assert any("regex-like call sub" in item for item in offenders)
    assert any("branches on LaTeX command" in item and "sideset" in item for item in offenders)
    assert any("command constant in container" in item and "prescript" in item for item in offenders)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _literal_offenders(relative_path: str, text: str) -> list[str]:
    offenders: list[str] = []
    for fragment in FORBIDDEN_LITERAL_FRAGMENTS:
        if fragment in text:
            offenders.append(f"{relative_path}: literal {fragment}")
    return offenders


def _structural_offenders(relative_path: str, text: str) -> list[str]:
    offenders: list[str] = []
    tree = ast.parse(text, filename=relative_path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
            if any(name in FORBIDDEN_PYTHON_IMPORTS for name in names):
                offenders.append(f"{relative_path}: imports regex module")
        if isinstance(node, ast.Call) and _call_name(node.func) in FORBIDDEN_CALL_NAMES:
            offenders.append(f"{relative_path}: regex-like call {_call_name(node.func)}")
        if isinstance(node, ast.Compare):
            text_values = [
                item
                for item in [node.left, *node.comparators]
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
            for item in text_values:
                value = str(item.value)
                if _is_forbidden_latex_command(value):
                    offenders.append(f"{relative_path}: branches on LaTeX command {value}")
        if isinstance(node, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
            for item in ast.walk(node):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    value = str(item.value)
                    if _is_forbidden_latex_command(value):
                        offenders.append(f"{relative_path}: command constant in container {value}")
        if isinstance(node, ast.Call):
            for item in ast.walk(node):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    value = str(item.value)
                    if _is_forbidden_latex_command(value):
                        offenders.append(f"{relative_path}: command constant in call {value}")
    return offenders


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_forbidden_latex_command(value: str) -> bool:
    if not value.startswith("\\"):
        return False
    if len(value) < 2 or not value[1].isalpha():
        return False
    if value in ALLOWED_COMMAND_CONSTANTS:
        return False
    return True
