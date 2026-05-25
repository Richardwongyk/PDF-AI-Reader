"""Deterministic LaTeX math span extraction for dataset ground truth.

This parser is for source-code datasets and audits, not for production PDF
formula recognition.  It scans TeX source character by character so comments,
escaped delimiters, display environments, and inline/display math delimiters
are handled consistently with original source offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Literal


LatexMathKind = Literal["display", "inline"]

DEFAULT_DISPLAY_ENVS = (
    "equation",
    "equation*",
    "align",
    "align*",
    "alignat",
    "alignat*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "flalign",
    "flalign*",
    "eqnarray",
    "eqnarray*",
    "split",
    "cases",
    "displaymath",
    "IEEEeqnarray",
)
DEFAULT_INLINE_ENVS = ("math",)
DEFAULT_SKIP_ENVS = (
    "verbatim",
    "Verbatim",
    "lstlisting",
    "minted",
    "comment",
    "filecontents",
    "filecontents*",
)
BEGIN_ENV_RE = re.compile(r"\\begin\s*\{([^{}]+)\}")


@dataclass(frozen=True)
class LatexMathSpan:
    kind: LatexMathKind
    body: str
    body_start: int
    body_end: int
    delimiter_start: int
    delimiter_end: int
    delimiter: str
    env: str

    @property
    def normalized_body(self) -> str:
        return self.body.strip()


def extract_latex_math_spans(
    text: str,
    *,
    display_envs: Iterable[str] = DEFAULT_DISPLAY_ENVS,
    inline_envs: Iterable[str] = DEFAULT_INLINE_ENVS,
    skip_envs: Iterable[str] = DEFAULT_SKIP_ENVS,
) -> list[LatexMathSpan]:
    """Extract all math spans in source order with original offsets."""

    display_set = set(display_envs)
    inline_set = set(inline_envs)
    skip_set = set(skip_envs)
    spans: list[LatexMathSpan] = []
    i = 0
    n = len(text)
    while i < n:
        if _starts_comment(text, i):
            i = _line_end(text, i)
            continue

        begin = BEGIN_ENV_RE.match(text, i)
        if begin is not None and not _is_escaped(text, i):
            env = begin.group(1)
            begin_end = begin.end()
            end_start, end_end = _find_env_end(text, env, begin_end)
            if end_start >= 0:
                if env in skip_set:
                    i = end_end
                    continue
                kind: LatexMathKind | None = None
                if env in display_set:
                    kind = "display"
                elif env in inline_set:
                    kind = "inline"
                if kind is not None:
                    body = text[begin_end:end_start]
                    spans.append(
                        LatexMathSpan(
                            kind=kind,
                            body=body,
                            body_start=begin_end,
                            body_end=end_start,
                            delimiter_start=i,
                            delimiter_end=end_end,
                            delimiter="environment",
                            env=env,
                        )
                    )
                    i = end_end
                    continue

        if text.startswith(r"\[", i) and not _is_escaped(text, i):
            close = _find_unescaped(text, r"\]", i + 2)
            if close >= 0:
                spans.append(_span(text, "display", i + 2, close, i, close + 2, r"\[...\]", "bracket_math"))
                i = close + 2
                continue

        if text.startswith(r"\(", i) and not _is_escaped(text, i):
            close = _find_unescaped(text, r"\)", i + 2)
            if close >= 0:
                spans.append(_span(text, "inline", i + 2, close, i, close + 2, r"\(...\)", "paren_inline"))
                i = close + 2
                continue

        if text.startswith("$$", i) and not _is_escaped(text, i):
            close = _find_unescaped(text, "$$", i + 2)
            if close >= 0:
                spans.append(_span(text, "display", i + 2, close, i, close + 2, "$$...$$", "dollar_display"))
                i = close + 2
                continue

        if text[i] == "$" and not text.startswith("$$", i) and not _is_escaped(text, i):
            close = _find_inline_dollar_close(text, i + 1)
            if close >= 0:
                spans.append(_span(text, "inline", i + 1, close, i, close + 1, "$...$", "dollar_inline"))
                i = close + 1
                continue

        i += 1
    return spans


def _span(
    text: str,
    kind: LatexMathKind,
    body_start: int,
    body_end: int,
    delimiter_start: int,
    delimiter_end: int,
    delimiter: str,
    env: str,
) -> LatexMathSpan:
    return LatexMathSpan(
        kind=kind,
        body=text[body_start:body_end],
        body_start=body_start,
        body_end=body_end,
        delimiter_start=delimiter_start,
        delimiter_end=delimiter_end,
        delimiter=delimiter,
        env=env,
    )


def _starts_comment(text: str, index: int) -> bool:
    return text[index] == "%" and not _is_escaped(text, index)


def _line_end(text: str, index: int) -> int:
    end = text.find("\n", index)
    return len(text) if end < 0 else end + 1


def _find_env_end(text: str, env: str, start: int) -> tuple[int, int]:
    needle = rf"\end{{{env}}}"
    pos = _find_unescaped(text, needle, start)
    return (pos, pos + len(needle)) if pos >= 0 else (-1, -1)


def _find_unescaped(text: str, needle: str, start: int) -> int:
    pos = text.find(needle, start)
    while pos >= 0:
        if not _is_escaped(text, pos):
            return pos
        pos = text.find(needle, pos + 1)
    return -1


def _find_inline_dollar_close(text: str, start: int) -> int:
    brace_depth = 0
    pos = text.find("$", start)
    cursor = start
    while pos >= 0:
        while cursor < pos:
            if not _is_escaped(text, cursor):
                if text[cursor] == "{":
                    brace_depth += 1
                elif text[cursor] == "}" and brace_depth > 0:
                    brace_depth -= 1
            cursor += 1
        if not _is_escaped(text, pos) and not text.startswith("$$", pos):
            if brace_depth == 0:
                return pos
        pos = text.find("$", pos + 1)
    return -1


def _is_escaped(text: str, index: int) -> bool:
    count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        count += 1
        cursor -= 1
    return count % 2 == 1
