r"""Lightweight LaTeX macro expansion for dataset gold labels.

This module is used only by training/audit data preparation.  It converts
project-specific source macros into canonical LaTeX targets so a model does not
learn private commands such as ``\pre``.  It is intentionally conservative:
macros that cannot be expanded safely produce warnings for review instead of
being silently accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


MACRO_EXPANSION_VERSION = "latex_macro_expander_v1"
PROTECTED_LATEX_COMMANDS = {
    r"\frac",
    r"\sqrt",
    r"\left",
    r"\right",
    r"\big",
    r"\Big",
    r"\bigg",
    r"\Bigg",
    r"\text",
    r"\mathrm",
    r"\mathbf",
    r"\mathbb",
    r"\mathcal",
    r"\mathfrak",
    r"\mathscr",
    r"\operatorname",
    r"\sum",
    r"\prod",
    r"\int",
    r"\lim",
    r"\sin",
    r"\cos",
    r"\tan",
    r"\log",
    r"\ln",
    r"\exp",
    r"\min",
    r"\max",
    r"\sup",
    r"\inf",
}
INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
USEPACKAGE_RE = re.compile(r"\\usepackage(?:\[[^\]]*\])?\s*\{([^{}]+)\}")
DOCUMENT_BEGIN_RE = re.compile(r"\\begin\s*\{document\}")


@dataclass(frozen=True)
class LatexMacroDefinition:
    name: str
    arg_count: int
    replacement: str
    source: str
    optional_default: str | None = None


@dataclass(frozen=True)
class LatexMacroExpansionResult:
    latex: str
    applied_macros: tuple[str, ...]
    warnings: tuple[str, ...]
    version: str = MACRO_EXPANSION_VERSION


class LatexMacroExpander:
    def __init__(self, definitions: dict[str, LatexMacroDefinition]) -> None:
        self.definitions = dict(definitions)

    def expand(self, text: str, *, max_passes: int = 12) -> LatexMacroExpansionResult:
        current = str(text or "")
        applied: list[str] = []
        warnings: list[str] = []
        for _pass in range(max(1, max_passes)):
            expanded, changed, pass_applied, pass_warnings = self._expand_once(current)
            applied.extend(pass_applied)
            warnings.extend(pass_warnings)
            current = expanded
            if not changed:
                break
        else:
            warnings.append("macro_expansion_pass_limit_reached")
        unresolved = sorted({name for name in self.definitions if name in _commands_in(current)})
        if unresolved:
            warnings.extend(f"unexpanded_custom_macro:{name}" for name in unresolved)
        return LatexMacroExpansionResult(
            latex=_clean_latex_spaces(current),
            applied_macros=tuple(sorted(set(applied))),
            warnings=tuple(sorted(set(warnings))),
        )

    def _expand_once(self, text: str) -> tuple[str, bool, list[str], list[str]]:
        out: list[str] = []
        applied: list[str] = []
        warnings: list[str] = []
        changed = False
        i = 0
        while i < len(text):
            if text[i] != "\\":
                out.append(text[i])
                i += 1
                continue
            name, command_end = _read_command_token(text, i)
            definition = self.definitions.get(name)
            if definition is None:
                out.append(text[i:command_end])
                i = command_end
                continue
            replacement, new_index, macro_warnings = _expand_invocation(text, command_end, definition)
            warnings.extend(macro_warnings)
            if replacement is None:
                out.append(text[i:command_end])
                i = command_end
                continue
            out.append(replacement)
            applied.append(name)
            changed = True
            i = new_index
        return "".join(out), changed, applied, warnings


def load_latex_macro_expander(latex_root: Path) -> LatexMacroExpander:
    definitions: dict[str, LatexMacroDefinition] = {}
    for path in _macro_source_files(latex_root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="ignore")
        for definition in parse_latex_macro_definitions(text, source=_relative_source(path, latex_root)):
            if definition.name in PROTECTED_LATEX_COMMANDS:
                continue
            definitions[definition.name] = definition
    return LatexMacroExpander(definitions)


def parse_latex_macro_definitions(text: str, *, source: str = "") -> list[LatexMacroDefinition]:
    clean = _strip_comments(text)
    definitions: list[LatexMacroDefinition] = []
    i = 0
    while i < len(clean):
        if (
            clean.startswith("\\newcommand", i)
            or clean.startswith("\\renewcommand", i)
            or clean.startswith("\\providecommand", i)
            or clean.startswith("\\DeclareRobustCommand", i)
        ):
            parsed = _parse_newcommand_like(clean, i, source)
            if parsed is not None:
                definition, i = parsed
                definitions.append(definition)
                continue
        if clean.startswith("\\DeclareMathOperator", i):
            parsed = _parse_math_operator(clean, i, source)
            if parsed is not None:
                definition, i = parsed
                definitions.append(definition)
                continue
        if clean.startswith("\\def", i):
            parsed = _parse_def(clean, i, source)
            if parsed is not None:
                definition, i = parsed
                definitions.append(definition)
                continue
        i += 1
    return definitions


def _parse_newcommand_like(text: str, start: int, source: str) -> tuple[LatexMacroDefinition, int] | None:
    _name, i = _read_command_token(text, start)
    if i < len(text) and text[i] == "*":
        i += 1
    i = _skip_spaces(text, i)
    macro_name, i = _read_definition_name(text, i)
    if not macro_name:
        return None
    i = _skip_spaces(text, i)
    arg_count = 0
    optional_default: str | None = None
    if i < len(text) and text[i] == "[":
        arg_text, next_i = _read_bracket_group(text, i)
        if arg_text is None:
            return None
        try:
            arg_count = int(arg_text.strip() or "0")
        except ValueError:
            return None
        i = _skip_spaces(text, next_i)
    if i < len(text) and text[i] == "[":
        optional_default, next_i = _read_bracket_group(text, i)
        if optional_default is None:
            return None
        i = _skip_spaces(text, next_i)
    replacement, next_i = _read_braced_group(text, i)
    if replacement is None:
        return None
    return LatexMacroDefinition(macro_name, arg_count, replacement, source, optional_default), next_i


def _parse_math_operator(text: str, start: int, source: str) -> tuple[LatexMacroDefinition, int] | None:
    command, i = _read_command_token(text, start)
    if i < len(text) and text[i] == "*":
        command += "*"
        i += 1
    i = _skip_spaces(text, i)
    macro_name, i = _read_definition_name(text, i)
    if not macro_name:
        return None
    i = _skip_spaces(text, i)
    operator, next_i = _read_braced_group(text, i)
    if operator is None:
        return None
    replacement = rf"\operatorname{{{operator}}}" if command.endswith("*") else rf"\operatorname{{{operator}}}"
    return LatexMacroDefinition(macro_name, 0, replacement, source), next_i


def _parse_def(text: str, start: int, source: str) -> tuple[LatexMacroDefinition, int] | None:
    _def, i = _read_command_token(text, start)
    i = _skip_spaces(text, i)
    macro_name, i = _read_command_token(text, i)
    if not macro_name.startswith("\\"):
        return None
    arg_count = 0
    while i < len(text) and text[i] != "{":
        if text[i] == "#" and i + 1 < len(text) and text[i + 1].isdigit():
            arg_count = max(arg_count, int(text[i + 1]))
            i += 2
            continue
        if not text[i].isspace():
            return None
        i += 1
    replacement, next_i = _read_braced_group(text, i)
    if replacement is None:
        return None
    return LatexMacroDefinition(macro_name, arg_count, replacement, source), next_i


def _expand_invocation(
    text: str,
    command_end: int,
    definition: LatexMacroDefinition,
) -> tuple[str | None, int, list[str]]:
    warnings: list[str] = []
    i = command_end
    args: list[str] = []
    if definition.optional_default is not None:
        i = _skip_spaces(text, i)
        if i < len(text) and text[i] == "[":
            optional_arg, i = _read_bracket_group(text, i)
            if optional_arg is None:
                return None, command_end, [f"macro_optional_arg_parse_failed:{definition.name}"]
            args.append(optional_arg)
        else:
            args.append(definition.optional_default)
    required_count = definition.arg_count - (1 if definition.optional_default is not None else 0)
    for _ in range(max(0, required_count)):
        i = _skip_spaces(text, i)
        arg, next_i = _read_tex_argument(text, i)
        if arg is None:
            return None, command_end, [f"macro_missing_arg:{definition.name}"]
        args.append(arg)
        i = next_i
    replacement = definition.replacement
    for index, arg in enumerate(args, start=1):
        replacement = replacement.replace(f"#{index}", arg)
    if "#" in replacement:
        warnings.append(f"macro_unresolved_parameter:{definition.name}")
    return replacement, i, warnings


def _read_definition_name(text: str, index: int) -> tuple[str, int]:
    index = _skip_spaces(text, index)
    if index < len(text) and text[index] == "{":
        content, next_i = _read_braced_group(text, index)
        return (content.strip() if content else "", next_i)
    return _read_command_token(text, index)


def _read_tex_argument(text: str, index: int) -> tuple[str | None, int]:
    if index >= len(text):
        return None, index
    if text[index] == "{":
        return _read_braced_group(text, index)
    if text[index] == "\\":
        return _read_command_token(text, index)
    return text[index], index + 1


def _read_command_token(text: str, index: int) -> tuple[str, int]:
    if index >= len(text) or text[index] != "\\":
        return "", index
    i = index + 1
    while i < len(text) and text[i].isalpha():
        i += 1
    if i == index + 1 and i < len(text):
        i += 1
    return text[index:i], i


def _read_braced_group(text: str, index: int) -> tuple[str | None, int]:
    return _read_balanced_group(text, index, "{", "}")


def _read_bracket_group(text: str, index: int) -> tuple[str | None, int]:
    return _read_balanced_group(text, index, "[", "]")


def _read_balanced_group(text: str, index: int, open_ch: str, close_ch: str) -> tuple[str | None, int]:
    if index >= len(text) or text[index] != open_ch:
        return None, index
    depth = 1
    i = index + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return text[index + 1 : i], i + 1
        i += 1
    return None, index


def _strip_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        escaped = False
        kept: list[str] = []
        for ch in line:
            if ch == "%" and not escaped:
                break
            kept.append(ch)
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        lines.append("".join(kept))
    return "\n".join(lines)


def _commands_in(text: str) -> set[str]:
    return set(re.findall(r"\\[A-Za-z]+|\\.", text))


def _clean_latex_spaces(text: str) -> str:
    return " ".join(str(text or "").split())


def _skip_spaces(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _macro_source_files(latex_root: Path) -> list[Path]:
    if not latex_root.exists():
        return []
    roots = _main_tex_files(latex_root)
    if not roots:
        roots = sorted(latex_root.glob("*.tex"))
    result: set[Path] = set()
    for root_tex in roots:
        result.update(_preamble_dependency_files(root_tex, latex_root, seen=set()))
    return sorted(result, key=str)


def _main_tex_files(latex_root: Path) -> list[Path]:
    result: list[Path] = []
    for path in sorted(latex_root.glob("*.tex")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DOCUMENT_BEGIN_RE.search(text):
            result.append(path)
    return result


def _preamble_dependency_files(path: Path, latex_root: Path, *, seen: set[Path]) -> set[Path]:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    if resolved in seen:
        return set()
    seen.add(resolved)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    preamble = _preamble_text(text)
    result = {path}
    for include_name in _dependency_names(preamble):
        dep = _resolve_tex_dependency(path.parent, latex_root, include_name)
        if dep is not None:
            result.update(_preamble_dependency_files(dep, latex_root, seen=seen))
    return result


def _preamble_text(text: str) -> str:
    match = DOCUMENT_BEGIN_RE.search(text)
    return text[: match.start()] if match else text


def _dependency_names(text: str) -> list[str]:
    names: list[str] = []
    for match in INPUT_RE.finditer(text):
        names.append(match.group(1))
    for match in USEPACKAGE_RE.finditer(text):
        names.extend(part.strip() for part in match.group(1).split(",") if part.strip())
    return names


def _resolve_tex_dependency(base_dir: Path, latex_root: Path, name: str) -> Path | None:
    raw = name.strip()
    if not raw:
        return None
    candidates: list[Path] = []
    raw_path = Path(raw)
    suffixes = [""] if raw_path.suffix else [".tex", ".sty", ".cls"]
    for suffix in suffixes:
        rel = Path(raw + suffix)
        candidates.append(base_dir / rel)
        candidates.append(latex_root / rel)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    matches = list(latex_root.rglob(raw_path.name + ("" if raw_path.suffix else ".tex")))
    matches.extend(latex_root.rglob(raw_path.name + ("" if raw_path.suffix else ".sty")))
    matches.extend(latex_root.rglob(raw_path.name + ("" if raw_path.suffix else ".cls")))
    return sorted(set(matches), key=str)[0] if matches else None


def _relative_source(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
