"""Audit PDF formula extraction against bundled LaTeX sources.

The report is intentionally diagnostic. It does not claim exact source/PDF
alignment; it gives a reproducible baseline for:
- how many formula-like snippets exist in the LaTeX source,
- how many formula/image blocks the current PDF parser finds,
- how many scanned/image formulas still need OCR,
- which frequent source math commands are missing from extracted PDF text.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.formula_detector import Pix2TextMFDDetector
from src.core.models import BlockType
from src.core.pdf_engine import DocumentChunker


DISPLAY_ENVS = (
    "equation",
    "equation*",
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "split",
    "cases",
)

MATH_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
SOURCE_FORMULA_PATTERNS = [
    re.compile(r"(?<!\\)\\\[(.+?)(?<!\\)\\\]", re.DOTALL),
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"(?<!\\)\\\((.+?)(?<!\\)\\\)", re.DOTALL),
    re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL),
]
LATEX_INCLUDE_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
DOCUMENT_BODY_RE = re.compile(r"\\begin\{document\}(.+?)\\end\{document\}", re.DOTALL)


@dataclass
class CasePaths:
    name: str
    pdf: Path
    latex_root: Path


@dataclass
class FormulaReport:
    name: str
    pdf: str
    latex_root: str
    elapsed_sec: float
    pages: int
    source_tex_files: int
    source_total_tex_files: int
    source_selected_tex_files: int
    source_coverage: dict[str, Any]
    source_formula_snippets: int
    source_display_snippets: int
    source_inline_snippets: int
    source_top_commands: list[tuple[str, int]]
    pdf_blocks: int
    pdf_formula_blocks: int
    pdf_image_blocks: int
    pdf_scanned_formula_blocks: int
    pdf_ocr_formula_blocks: int
    pdf_needs_ocr_blocks: int
    pdf_top_commands: list[tuple[str, int]]
    missing_common_source_commands: list[str]
    recovered_common_source_commands: list[str]
    common_source_command_recall: float
    source_exact_match_count: int
    source_near_match_count: int
    source_weak_match_count: int
    source_unmatched_count: int
    source_near_match_rate: float
    source_weak_match_rate: float
    inline_source_near_match_rate: float
    inline_source_weak_match_rate: float
    inline_source_unmatched_count: int
    average_best_similarity: float
    low_similarity_pdf_formula_count: int
    sample_source_formulas: list[str]
    sample_pdf_formulas: list[str]
    sample_source_unmatched: list[dict[str, Any]]
    sample_pdf_low_similarity: list[dict[str, Any]]
    sample_needs_ocr_blocks: list[dict[str, Any]]
    born_digital_diagnostics: dict[str, Any]
    quality_gate: dict[str, Any]


@dataclass
class SourceFormulaExtraction:
    display: list[str]
    inline: list[str]
    tex_files: int
    total_tex_files: int
    selected_tex_files: int
    coverage: dict[str, Any]


@dataclass(frozen=True)
class SourceTexEntry:
    path: Path
    text: str
    order: int
    title: str = ""
    toc_page: int | None = None
    effective_page: int | None = None


def _cases() -> list[CasePaths]:
    test_dir = ROOT / "测试资料"
    return [
        CasePaths(
            name="attention",
            pdf=test_dir / "Attention is all you need.pdf",
            latex_root=test_dir / "Attention is all you need LaTeX源代码和资料，用于与PDF版是否扫描正确进行对照",
        ),
        CasePaths(
            name="napkin",
            pdf=test_dir / "Napkin.pdf",
            latex_root=test_dir / "Napkin LaTeX源代码，用于和原版PDF对照",
        ),
    ]


def _strip_comments(tex: str) -> str:
    lines: list[str] = []
    for line in tex.splitlines():
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


def _extract_source_formulas(latex_root: Path) -> tuple[list[str], list[str], int]:
    extraction = _extract_source_formulas_detailed(latex_root)
    return extraction.display, extraction.inline, extraction.tex_files


def _extract_source_formulas_detailed(
    latex_root: Path,
    pdf: Path | None = None,
    max_pages: int = 0,
) -> SourceFormulaExtraction:
    entries = _ordered_source_entries(latex_root)
    all_source_files = _all_source_tex_files(latex_root)
    selected_entries, coverage = _select_source_entries_for_pdf_pages(
        entries,
        latex_root=latex_root,
        pdf=pdf,
        max_pages=max_pages,
    )

    display: list[str] = []
    inline: list[str] = []
    for entry in selected_entries:
        text = entry.text
        for env in DISPLAY_ENVS:
            escaped_env = re.escape(env)
            pattern = re.compile(
                rf"\\begin\{{{escaped_env}\}}(.+?)\\end\{{{escaped_env}\}}",
                re.DOTALL,
            )
            display.extend(m.group(1).strip() for m in pattern.finditer(text))
        display.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[0].finditer(text))
        display.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[1].finditer(text))
        inline.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[2].finditer(text))
        inline.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[3].finditer(text))

    return SourceFormulaExtraction(
        display=display,
        inline=inline,
        tex_files=len(selected_entries),
        total_tex_files=len(all_source_files),
        selected_tex_files=len(selected_entries),
        coverage=coverage,
    )


def _all_source_tex_files(latex_root: Path) -> list[Path]:
    return sorted(
        path
        for path in latex_root.rglob("*.tex")
        if path.is_file() and "build" not in path.relative_to(latex_root).parts
    )


def _ordered_source_entries(latex_root: Path) -> list[SourceTexEntry]:
    main = _find_main_tex(latex_root)
    if main is None:
        return [
            SourceTexEntry(
                path=path,
                text=_read_tex(path),
                order=index,
                title=_source_title(_read_tex(path)),
            )
            for index, path in enumerate(_all_source_tex_files(latex_root))
        ]

    entries: list[SourceTexEntry] = []
    visited: set[Path] = set()

    def append_entry(path: Path, text: str) -> None:
        if not text.strip():
            return
        entries.append(
            SourceTexEntry(
                path=path,
                text=text,
                order=len(entries),
                title=_source_title(text),
            )
        )

    def walk(path: Path, body_only: bool = False) -> None:
        resolved = path.resolve()
        if resolved in visited:
            return
        visited.add(resolved)
        text = _read_tex(path)
        if body_only:
            text = _document_body(text)
        append_entry(path, text)
        for target in LATEX_INCLUDE_RE.findall(_strip_comments(text)):
            child = _resolve_tex_include(latex_root, path.parent, target)
            if child is not None:
                walk(child)

    walk(main, body_only=True)
    return entries


def _find_main_tex(latex_root: Path) -> Path | None:
    candidates = [
        path
        for path in _all_source_tex_files(latex_root)
        if r"\begin{document}" in _read_tex(path)
    ]
    if not candidates:
        return None
    root_hint = _normalize_title(latex_root.name)
    return sorted(
        candidates,
        key=lambda path: (
            0 if _normalize_title(path.stem) and _normalize_title(path.stem) in root_hint else 1,
            len(path.relative_to(latex_root).parts),
            str(path.relative_to(latex_root)).lower(),
        ),
    )[0]


def _read_tex(path: Path) -> str:
    try:
        return _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _document_body(text: str) -> str:
    match = DOCUMENT_BODY_RE.search(text)
    return match.group(1) if match else text


def _resolve_tex_include(latex_root: Path, current_dir: Path, target: str) -> Path | None:
    raw = target.strip()
    if not raw:
        return None
    candidate_names = [raw]
    if not raw.lower().endswith(".tex"):
        candidate_names.append(f"{raw}.tex")
    for name in candidate_names:
        candidate = Path(name)
        paths = []
        if candidate.is_absolute():
            paths.append(candidate)
        else:
            paths.extend((current_dir / candidate, latex_root / candidate))
        for path in paths:
            if path.is_file():
                return path
    return None


def _source_title(text: str) -> str:
    match = re.search(
        r"\\(?:chapter|section)\*?(?:\[[^\]]*\])?\s*\{([^{}]+)\}",
        text,
        re.DOTALL,
    )
    if match is None:
        return ""
    return _compact_latex_title(match.group(1))


def _compact_latex_title(text: str) -> str:
    value = re.sub(r"\$[^$]*\$", "", text)
    value = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{([^{}]*)\})?", r"\1", value)
    value = re.sub(r"[{}]", "", value)
    return " ".join(value.split())


def _select_source_entries_for_pdf_pages(
    entries: list[SourceTexEntry],
    latex_root: Path,
    pdf: Path | None,
    max_pages: int,
) -> tuple[list[SourceTexEntry], dict[str, Any]]:
    if max_pages <= 0 or pdf is None:
        return entries, _source_coverage_payload(
            entries,
            entries,
            latex_root,
            method="ordered_main_tex" if _find_main_tex(latex_root) else "all_tex_files",
            max_pages=max_pages,
            toc_available=False,
        )

    toc_pages = _pdf_toc_title_pages(pdf)
    if not toc_pages:
        return entries, _source_coverage_payload(
            entries,
            entries,
            latex_root,
            method="ordered_main_tex_no_pdf_toc",
            max_pages=max_pages,
            toc_available=False,
        )

    annotated = _entries_with_toc_pages(entries, toc_pages)
    selected = [
        entry
        for entry in annotated
        if entry.effective_page is None or entry.effective_page <= max_pages
    ]
    return selected, _source_coverage_payload(
        annotated,
        selected,
        latex_root,
        method="ordered_main_tex_pdf_toc_file_coverage",
        max_pages=max_pages,
        toc_available=True,
    )


def _entries_with_toc_pages(
    entries: list[SourceTexEntry],
    toc_pages: dict[str, int],
) -> list[SourceTexEntry]:
    annotated: list[SourceTexEntry] = []
    current_page: int | None = None
    for entry in entries:
        toc_page = _match_title_page(entry.title, toc_pages) if entry.title else None
        if toc_page is not None:
            current_page = toc_page
        annotated.append(
            SourceTexEntry(
                path=entry.path,
                text=entry.text,
                order=entry.order,
                title=entry.title,
                toc_page=toc_page,
                effective_page=toc_page if toc_page is not None else current_page,
            )
        )
    return annotated


def _pdf_toc_title_pages(pdf: Path) -> dict[str, int]:
    try:
        doc = fitz.open(pdf)
    except Exception:
        return {}
    try:
        pages: dict[str, int] = {}
        for _level, title, page in doc.get_toc(simple=True):
            normalized = _normalize_title(str(title))
            if normalized and int(page) > 0:
                pages.setdefault(normalized, int(page))
        return pages
    finally:
        doc.close()


def _match_title_page(title: str, toc_pages: dict[str, int]) -> int | None:
    normalized = _normalize_title(title)
    if not normalized:
        return None
    if normalized in toc_pages:
        return toc_pages[normalized]
    best_title = ""
    best_score = 0.0
    for candidate in toc_pages:
        score = difflib.SequenceMatcher(None, normalized, candidate).ratio()
        if score > best_score:
            best_score = score
            best_title = candidate
    if best_score >= 0.88:
        return toc_pages[best_title]
    return None


def _normalize_title(text: str) -> str:
    text = _compact_latex_title(str(text or "")).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _source_coverage_payload(
    entries: list[SourceTexEntry],
    selected: list[SourceTexEntry],
    latex_root: Path,
    method: str,
    max_pages: int,
    toc_available: bool,
) -> dict[str, Any]:
    selected_paths = {entry.path for entry in selected}
    mapped = [entry for entry in entries if entry.toc_page is not None]
    unmapped = [entry for entry in entries if entry.toc_page is None]
    excluded = [entry for entry in entries if entry.path not in selected_paths]
    return {
        "method": method,
        "max_pages": max_pages,
        "toc_available": toc_available,
        "ordered_files": len(entries),
        "selected_files": len(selected),
        "excluded_files": len(excluded),
        "toc_mapped_files": len(mapped),
        "toc_unmapped_files": len(unmapped),
        "selected_sample": [_relative_source_path(entry.path, latex_root) for entry in selected[:12]],
        "excluded_sample": [_relative_source_path(entry.path, latex_root) for entry in excluded[:12]],
        "toc_mapped_sample": [
            {
                "path": _relative_source_path(entry.path, latex_root),
                "title": entry.title,
                "page": entry.toc_page,
            }
            for entry in mapped[:12]
        ],
        "note": (
            "Page-limited LaTeX coverage is selected at source-file granularity "
            "using the PDF outline. It is a fairer diagnostic baseline than "
            "comparing a partial PDF parse with the entire source tree, but it "
            "is not line-level SyncTeX alignment."
        ),
    }


def _relative_source_path(path: Path, latex_root: Path) -> str:
    try:
        return str(path.relative_to(latex_root))
    except ValueError:
        return str(path)


def _command_counts(snippets: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for snippet in snippets:
        counts.update(_canonical_math_command(command) for command in MATH_COMMAND_RE.findall(snippet))
    return counts


def _canonical_math_command(command: str) -> str:
    if command in {r"\text", r"\operatorname"}:
        return r"\mathrm"
    return command


MACRO_EXPANSIONS = {
    r"\dmodel": r"d_{\text{model}}",
    r"\dff": r"d_{\text{ff}}",
    r"\dffn": r"d_{\text{ffn}}",
    r"\vec": r"\mathbf",
    r"\mbf": r"\mathbf",
    r"\mc": r"\mathcal",
    r"\RR": r"\mathbb{R}",
    r"\CC": r"\mathbb{C}",
    r"\ZZ": r"\mathbb{Z}",
    r"\QQ": r"\mathbb{Q}",
    r"\NN": r"\mathbb{N}",
    r"\kp": r"\mathfrak{p}",
    r"\kq": r"\mathfrak{q}",
    r"\km": r"\mathfrak{m}",
    r"\OO": r"\mathcal{O}",
    r"\AA": r"\mathcal{A}",
    r"\BB": r"\mathcal{B}",
    r"\VV": r"\mathcal{V}",
}


GREEK_WORDS = {
    "alpha": "a",
    "beta": "b",
    "gamma": "g",
    "delta": "d",
    "epsilon": "e",
    "theta": "theta",
    "lambda": "lambda",
    "mu": "mu",
    "pi": "pi",
    "sigma": "sigma",
    "phi": "phi",
    "omega": "omega",
    "xi": "xi",
    "tau": "tau",
}


def _normalize_formula_for_match(text: str) -> str:
    """Normalize LaTeX/PDF formula text for coarse source-vs-OCR matching."""
    normalized = str(text or "")
    normalized = normalized.replace("−", "-").replace("·", r"\cdot")
    normalized = normalized.replace("∈", r"\in").replace("×", r"\times")
    for macro, expansion in MACRO_EXPANSIONS.items():
        normalized = normalized.replace(macro, expansion)
    normalized = re.sub(
        r"\\(?:mathrm|operatorname\*?|text|mathbf|mathbb|mathcal|mathfrak)\s*\{([^{}]*)\}",
        r"\1",
        normalized,
    )
    normalized = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\b", "", normalized)
    normalized = re.sub(r"\\(?:tiny|small|qquad|quad)\b", "", normalized)
    normalized = re.sub(r"\\[,;! ]", "", normalized)
    for command, value in GREEK_WORDS.items():
        normalized = normalized.replace(f"\\{command}", value)
    normalized = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"\1/\2", normalized)
    normalized = re.sub(r"\\sqrt\s*\{([^{}]+)\}", r"sqrt\1", normalized)
    normalized = re.sub(r"\\[A-Za-z]+", "", normalized)
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9=+\-*/^_(){}\[\].,|<>:]+", "", normalized)
    normalized = re.sub(r"([a-z])\s+(?=[a-z])", r"\1", normalized)
    return normalized


def _best_formula_matches(
    source_formulas: list[str],
    pdf_formulas: list[str],
    max_sources: int = 5000,
    max_candidates_per_source: int = 60,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    source_norms = [
        _normalize_formula_for_match(item)
        for item in source_formulas[:max_sources]
    ]
    pdf_norms = [_normalize_formula_for_match(item) for item in pdf_formulas]
    source_tokens = [_match_tokens(source_norm) for source_norm in source_norms]
    pdf_tokens = [_match_tokens(pdf_norm) for pdf_norm in pdf_norms]
    pdf_index: dict[str, list[int]] = {}
    for pdf_index_id, tokens in enumerate(pdf_tokens):
        for token in tokens:
            pdf_index.setdefault(token, []).append(pdf_index_id)
    source_index: dict[str, list[int]] = {}
    for source_index_id, tokens in enumerate(source_tokens):
        for token in tokens:
            source_index.setdefault(token, []).append(source_index_id)
    matches: list[dict[str, Any]] = []
    for index, (source, source_norm, tokens) in enumerate(
        zip(source_formulas[:max_sources], source_norms, source_tokens, strict=False)
    ):
        if len(source_norm) < 4:
            continue
        candidate_ids = _candidate_ids(
            source_norm,
            tokens,
            pdf_norms,
            pdf_index,
            max_candidates_per_source,
        )
        best_score = 0.0
        best_pdf = ""
        best_pdf_index = -1
        for pdf_index_id in candidate_ids:
            pdf = pdf_formulas[pdf_index_id]
            pdf_norm = pdf_norms[pdf_index_id]
            if len(pdf_norm) < 4:
                continue
            score = _formula_similarity(source_norm, pdf_norm)
            if score > best_score:
                best_score = score
                best_pdf = pdf
                best_pdf_index = pdf_index_id
        matches.append({
            "source_index": index,
            "source": " ".join(source.split())[:240],
            "pdf_index": best_pdf_index,
            "pdf": " ".join(best_pdf.split())[:240],
            "similarity": round(best_score, 3),
        })
    low_pdf: list[dict[str, Any]] = []
    for pdf_index_id, (pdf, pdf_norm, tokens) in enumerate(
        zip(pdf_formulas, pdf_norms, pdf_tokens, strict=False)
    ):
        if len(pdf_norm) < 4:
            continue
        best_score = 0.0
        source_candidate_ids = _candidate_ids(
            pdf_norm,
            tokens,
            source_norms,
            source_index,
            max_candidates_per_source,
        )
        for source_id in source_candidate_ids:
            source_norm = source_norms[source_id]
            if len(source_norm) < 4:
                continue
            best_score = max(best_score, _formula_similarity(source_norm, pdf_norm))
        if best_score < 0.45:
            low_pdf.append({
                "pdf_index": pdf_index_id,
                "pdf": " ".join(pdf.split())[:240],
                "best_similarity": round(best_score, 3),
            })
    exact = sum(1 for item in matches if item["similarity"] >= 0.98)
    near = sum(1 for item in matches if item["similarity"] >= 0.80)
    weak = sum(1 for item in matches if item["similarity"] >= 0.55)
    total = len(matches)
    metrics = {
        "exact": exact,
        "near": near,
        "weak": weak,
        "unmatched": max(total - weak, 0),
        "near_rate": near / total if total else 1.0,
        "weak_rate": weak / total if total else 1.0,
        "average": sum(float(item["similarity"]) for item in matches) / total if total else 1.0,
    }
    return matches, low_pdf, metrics


def _candidate_ids(
    query_norm: str,
    query_tokens: set[str],
    candidate_norms: list[str],
    token_index: dict[str, list[int]],
    limit: int,
) -> list[int]:
    """Return the most plausible candidates using token overlap before edit distance."""
    if not candidate_norms:
        return []
    counts: Counter[int] = Counter()
    for token in query_tokens:
        counts.update(token_index.get(token, []))
    if not counts:
        return list(range(min(len(candidate_norms), limit)))
    ranked = sorted(
        counts,
        key=lambda idx: (
            -counts[idx],
            abs(len(candidate_norms[idx]) - len(query_norm)),
            idx,
        ),
    )
    return ranked[:limit]


def _formula_similarity(left: str, right: str) -> float:
    score = difflib.SequenceMatcher(None, left, right).ratio()
    if left in right or right in left:
        score = max(score, min(len(left), len(right)) / max(len(left), len(right)))
    return score


def _match_tokens(normalized_formula: str) -> set[str]:
    tokens = set(re.findall(r"[a-z]{2,}|[0-9]+", normalized_formula))
    compact = normalized_formula.replace("_", "").replace("{", "").replace("}", "")
    for length in (4, 6, 8):
        if len(compact) >= length:
            tokens.add(compact[:length])
    return tokens


def _parse_pdf_blocks(
    pdf: Path,
    run_mfd: bool,
    mfd_pages: list[int] | None,
    born_digital_math: bool = False,
    born_digital_semantics: bool = False,
    legacy_formula_heuristic: bool = True,
) -> tuple[int, list[Any]]:
    doc = fitz.open(pdf)
    try:
        chunker = DocumentChunker(
            enable_born_digital_math=born_digital_math,
            enable_born_digital_semantics=born_digital_semantics,
            enable_legacy_formula_heuristic=legacy_formula_heuristic,
        )
        blocks = chunker.chunk(doc)
        if run_mfd:
            detector = Pix2TextMFDDetector(dpi=200, max_mfd_pages=-1)
            if mfd_pages is not None:
                formulas = detector.detect_specific_pages(doc, mfd_pages)
                original = detector.detect_specific_pages
                detector.detect_specific_pages = lambda _doc, _pages: formulas  # type: ignore[method-assign]
                try:
                    blocks = detector.apply_to_blocks(blocks, doc)
                finally:
                    detector.detect_specific_pages = original  # type: ignore[method-assign]
            else:
                blocks = detector.apply_to_blocks(blocks, doc)
        return doc.page_count, blocks
    finally:
        doc.close()


def _parse_pdf_blocks_limited(
    pdf: Path,
    run_mfd: bool,
    mfd_pages: list[int] | None,
    max_pages: int = 0,
    born_digital_math: bool = False,
    born_digital_semantics: bool = False,
    legacy_formula_heuristic: bool = True,
) -> tuple[int, list[Any]]:
    if max_pages <= 0:
        return _parse_pdf_blocks(
            pdf,
            run_mfd=run_mfd,
            mfd_pages=mfd_pages,
            born_digital_math=born_digital_math,
            born_digital_semantics=born_digital_semantics,
            legacy_formula_heuristic=legacy_formula_heuristic,
        )
    doc = fitz.open(pdf)
    try:
        chunker = DocumentChunker(
            enable_born_digital_math=born_digital_math,
            enable_born_digital_semantics=born_digital_semantics,
            enable_legacy_formula_heuristic=legacy_formula_heuristic,
        )
        page_limit = min(doc.page_count, max_pages)
        blocks = []
        for page_num in range(page_limit):
            blocks.extend(chunker.chunk_page(doc, page_num))
        if run_mfd:
            detector = Pix2TextMFDDetector(dpi=200, max_mfd_pages=-1)
            pages = mfd_pages if mfd_pages is not None else list(range(page_limit))
            pages = [page for page in pages if 0 <= page < page_limit]
            formulas = detector.detect_specific_pages(doc, pages)
            original = detector.detect_specific_pages
            detector.detect_specific_pages = lambda _doc, _pages: formulas  # type: ignore[method-assign]
            try:
                blocks = detector.apply_to_blocks(blocks, doc)
            finally:
                detector.detect_specific_pages = original  # type: ignore[method-assign]
        return doc.page_count, blocks
    finally:
        doc.close()


def _sample(items: list[str], limit: int = 8) -> list[str]:
    compact = [" ".join(item.split()) for item in items if item and item.strip()]
    return [item[:240] for item in compact[:limit]]


def _audit_case(
    case: CasePaths,
    run_mfd: bool,
    mfd_pages: list[int] | None,
    max_pages: int = 0,
    max_match_candidates: int = 60,
    min_command_recall: float = 0.0,
    min_weak_match_rate: float = 0.0,
    max_low_similarity_pdf_rate: float = 1.0,
    born_digital_math: bool = False,
    born_digital_semantics: bool = False,
    legacy_formula_heuristic: bool = True,
    match_scope: str = "all",
) -> FormulaReport:
    start = time.perf_counter()
    if not case.pdf.exists():
        raise FileNotFoundError(case.pdf)
    if not case.latex_root.exists():
        raise FileNotFoundError(case.latex_root)

    source_extraction = _extract_source_formulas_detailed(
        case.latex_root,
        pdf=case.pdf,
        max_pages=max_pages,
    )
    source_display = source_extraction.display
    source_inline = source_extraction.inline
    if match_scope == "display":
        source_formulas = source_display
    elif match_scope == "inline":
        source_formulas = source_inline
    else:
        source_formulas = source_display + source_inline
    source_commands = _command_counts(source_formulas)

    page_count, blocks = _parse_pdf_blocks_limited(
        case.pdf,
        run_mfd=run_mfd,
        mfd_pages=mfd_pages,
        max_pages=max_pages,
        born_digital_math=born_digital_math,
        born_digital_semantics=born_digital_semantics,
        legacy_formula_heuristic=legacy_formula_heuristic,
    )
    formula_blocks = [b for b in blocks if b.block_type == BlockType.FORMULA]
    image_blocks = [b for b in blocks if b.block_type == BlockType.IMAGE]
    scanned_blocks = [
        b for b in formula_blocks
        if b.metadata.get("source") == "image_or_scan"
    ]
    ocr_blocks = [
        b for b in formula_blocks
        if b.metadata.get("mfr_recognized") or b.metadata.get("formula_ocr")
    ]
    needs_ocr = [
        b for b in formula_blocks
        if b.metadata.get("needs_ocr")
    ]
    born_digital_diagnostics = _born_digital_diagnostics(formula_blocks)
    pdf_commands = _command_counts([b.content for b in formula_blocks])
    pdf_formula_texts = [b.content for b in formula_blocks]
    similarity_matches, low_similarity_pdf, similarity_metrics = _best_formula_matches(
        source_formulas,
        pdf_formula_texts,
        max_candidates_per_source=max_match_candidates,
    )
    _, _, inline_similarity_metrics = _best_formula_matches(
        source_inline,
        pdf_formula_texts,
        max_candidates_per_source=max_match_candidates,
    )
    source_common = {
        cmd for cmd, count in source_commands.items()
        if count >= 2 and cmd not in {r"\label", r"\ref", r"\cite", r"\begin", r"\end"}
    }
    recovered = sorted(cmd for cmd in source_common if cmd in pdf_commands)
    missing = sorted(cmd for cmd in source_common if cmd not in pdf_commands)[:40]
    recall = len(recovered) / len(source_common) if source_common else 1.0
    weak_rate = float(similarity_metrics["weak_rate"])
    low_similarity_pdf_rate = len(low_similarity_pdf) / len(pdf_formula_texts) if pdf_formula_texts else 0.0
    violations: list[str] = []
    if recall < min_command_recall:
        violations.append(
            f"common_source_command_recall {recall:.3f} < {min_command_recall:.3f}"
        )
    if weak_rate < min_weak_match_rate:
        violations.append(
            f"source_weak_match_rate {weak_rate:.3f} < {min_weak_match_rate:.3f}"
        )
    if low_similarity_pdf_rate > max_low_similarity_pdf_rate:
        violations.append(
            f"low_similarity_pdf_rate {low_similarity_pdf_rate:.3f} > {max_low_similarity_pdf_rate:.3f}"
        )

    return FormulaReport(
        name=case.name,
        pdf=str(case.pdf),
        latex_root=str(case.latex_root),
        elapsed_sec=round(time.perf_counter() - start, 3),
        pages=page_count,
        source_tex_files=source_extraction.tex_files,
        source_total_tex_files=source_extraction.total_tex_files,
        source_selected_tex_files=source_extraction.selected_tex_files,
        source_coverage=source_extraction.coverage,
        source_formula_snippets=len(source_formulas),
        source_display_snippets=len(source_display),
        source_inline_snippets=len(source_inline),
        source_top_commands=source_commands.most_common(25),
        pdf_blocks=len(blocks),
        pdf_formula_blocks=len(formula_blocks),
        pdf_image_blocks=len(image_blocks),
        pdf_scanned_formula_blocks=len(scanned_blocks),
        pdf_ocr_formula_blocks=len(ocr_blocks),
        pdf_needs_ocr_blocks=len(needs_ocr),
        pdf_top_commands=pdf_commands.most_common(25),
        missing_common_source_commands=missing,
        recovered_common_source_commands=recovered,
        common_source_command_recall=round(recall, 3),
        source_exact_match_count=int(similarity_metrics["exact"]),
        source_near_match_count=int(similarity_metrics["near"]),
        source_weak_match_count=int(similarity_metrics["weak"]),
        source_unmatched_count=int(similarity_metrics["unmatched"]),
        source_near_match_rate=round(float(similarity_metrics["near_rate"]), 3),
        source_weak_match_rate=round(float(similarity_metrics["weak_rate"]), 3),
        inline_source_near_match_rate=round(float(inline_similarity_metrics["near_rate"]), 3),
        inline_source_weak_match_rate=round(float(inline_similarity_metrics["weak_rate"]), 3),
        inline_source_unmatched_count=int(inline_similarity_metrics["unmatched"]),
        average_best_similarity=round(float(similarity_metrics["average"]), 3),
        low_similarity_pdf_formula_count=len(low_similarity_pdf),
        sample_source_formulas=_sample(source_formulas),
        sample_pdf_formulas=_sample([b.content for b in formula_blocks]),
        sample_source_unmatched=[
            item for item in similarity_matches
            if item["similarity"] < 0.55
        ][:10],
        sample_pdf_low_similarity=low_similarity_pdf[:10],
        sample_needs_ocr_blocks=[
            {
                "id": b.id,
                "page": b.page_num + 1,
                "bbox": b.bbox,
                "content": b.content[:120],
                "metadata": b.metadata,
            }
            for b in needs_ocr[:10]
        ],
        born_digital_diagnostics=born_digital_diagnostics,
        quality_gate={
            "enabled": any((
                min_command_recall > 0,
                min_weak_match_rate > 0,
                max_low_similarity_pdf_rate < 1,
            )),
            "min_command_recall": min_command_recall,
            "min_weak_match_rate": min_weak_match_rate,
            "max_low_similarity_pdf_rate": max_low_similarity_pdf_rate,
            "low_similarity_pdf_rate": round(low_similarity_pdf_rate, 3),
            "violations": violations,
            "passed": not violations,
        },
    )


def _born_digital_diagnostics(formula_blocks: list[Any]) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    for block in formula_blocks:
        metadata = getattr(block, "metadata", {}) or {}
        diagnostic = metadata.get("born_digital_diagnostics")
        if isinstance(diagnostic, dict):
            diagnostics.append({
                "id": getattr(block, "id", ""),
                "page": int(getattr(block, "page_num", 0)) + 1,
                **diagnostic,
            })
    classifications = Counter(str(item.get("classification", "")) for item in diagnostics)
    risks: Counter[str] = Counter()
    for item in diagnostics:
        for risk in item.get("risks", []) or []:
            risks[str(risk)] += 1
    review = [
        item for item in diagnostics
        if item.get("classification") != "formula_candidate"
    ]
    return {
        "available": bool(diagnostics),
        "count": len(diagnostics),
        "classifications": dict(classifications),
        "risks": dict(risks),
        "sample_review": review[:10],
    }


def _parse_page_list(value: str) -> list[int] | None:
    if not value:
        return None
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            pages.update(range(start - 1, end))
        else:
            pages.add(int(part) - 1)
    return sorted(p for p in pages if p >= 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit formula extraction against LaTeX sources.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--mfd", action="store_true", help="Run Pix2Text MFD/MFR on the selected pages.")
    parser.add_argument(
        "--mfd-pages",
        default="",
        help="1-based page list/ranges for --mfd, for example '1,3,8-10'. Empty means all pages.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "test_artifacts" / "formula_audit.json",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Only parse the first N PDF pages for fast large-document audits. 0 means all pages.",
    )
    parser.add_argument(
        "--max-match-candidates",
        type=int,
        default=60,
        help="Similarity candidates per formula after token filtering. Raise for slower, less conservative audits.",
    )
    parser.add_argument(
        "--quality-gate",
        action="store_true",
        help="Fail with exit code 1 if formula/LaTeX quality thresholds are not met.",
    )
    parser.add_argument(
        "--born-digital-math",
        action="store_true",
        help="Also add display formula blocks from MuPDF rawdict structure facts. No OCR is used.",
    )
    parser.add_argument(
        "--born-digital-semantics",
        action="store_true",
        help="Recover evidence-backed LaTeX for born-digital display formula blocks.",
    )
    parser.add_argument(
        "--no-legacy-formula-heuristic",
        action="store_true",
        help="Disable the old span-level formula classifier for comparison.",
    )
    parser.add_argument(
        "--match-scope",
        choices=["all", "display", "inline"],
        default="all",
        help="Which LaTeX source formulas to use for similarity and quality gates.",
    )
    parser.add_argument("--min-command-recall", type=float, default=0.35)
    parser.add_argument("--min-weak-match-rate", type=float, default=0.35)
    parser.add_argument("--max-low-similarity-pdf-rate", type=float, default=0.60)
    args = parser.parse_args()

    selected = [case for case in _cases() if args.case in ("all", case.name)]
    mfd_pages = _parse_page_list(args.mfd_pages)
    reports = [
        _audit_case(
            case,
            run_mfd=args.mfd,
            mfd_pages=mfd_pages,
            max_pages=max(0, args.max_pages),
            max_match_candidates=max(1, args.max_match_candidates),
            min_command_recall=args.min_command_recall if args.quality_gate else 0.0,
            min_weak_match_rate=args.min_weak_match_rate if args.quality_gate else 0.0,
            max_low_similarity_pdf_rate=args.max_low_similarity_pdf_rate if args.quality_gate else 1.0,
            born_digital_math=args.born_digital_math,
            born_digital_semantics=args.born_digital_semantics,
            legacy_formula_heuristic=not args.no_legacy_formula_heuristic,
            match_scope=args.match_scope,
        )
        for case in selected
    ]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mfd_enabled": args.mfd,
        "born_digital_math_enabled": args.born_digital_math,
        "born_digital_semantics_enabled": args.born_digital_semantics,
        "legacy_formula_heuristic_enabled": not args.no_legacy_formula_heuristic,
        "mfd_pages": [p + 1 for p in mfd_pages] if mfd_pages is not None else None,
        "max_pages": max(0, args.max_pages),
        "max_match_candidates": max(1, args.max_match_candidates),
        "match_scope": args.match_scope,
        "reports": [asdict(report) for report in reports],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.quality_gate and any(not report.quality_gate["passed"] for report in reports):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
