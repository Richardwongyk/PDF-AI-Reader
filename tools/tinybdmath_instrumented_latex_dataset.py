"""Create exact source-to-PDF formula boxes by instrumenting LaTeX colors.

This is a dataset-only tool for PDFs whose LaTeX source is available.  It does
not OCR and does not infer formulas with handwritten parsing rules.  Instead it
uses the source as ground truth, wraps each math span in a unique LaTeX color,
compiles a temporary copy, and then reads the resulting PDF structure layer to
recover the exact colored glyph/vector bbox for each formula.

The original source tree is never modified.  The colored build lives under the
chosen output directory and can be deleted after the JSONL dataset is produced.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.latex_macro_expander import load_latex_macro_expander
from src.core.latex_math_source_parser import extract_latex_math_spans
from tools.born_digital_formula_dataset import (
    SourceFormulaRecord,
    _entry_page_windows,
    _select_cases,
    custom_case,
)
from tools.formula_latex_audit import _normalize_formula_for_match, _ordered_source_entries
from tools.tinybdmath_synctex_dataset import _pdf_alignment


SCHEMA_VERSION = "tinybdmath_instrumented_latex_dataset_v1"
BOX_SCHEMA_VERSION = "tinybdmath_instrumented_formula_box_v1"
TRAINING_ROW_SCHEMA_VERSION = "tinybdmath_instrumented_training_row_v1"
CAPTURE_QUALITY_VERSION = "instrumented_capture_quality_v2_unique_color_components"
MARKER_COMMAND = r"\pdfaireaderdatasetmarker"
MARKER_PREAMBLE = r"""
% PDF AI Reader dataset instrumentation. Inserted only in a temporary source copy.
\providecommand{\pdfaireaderdatasetmarker}[2]{{\color[HTML]{#1}#2}}
"""
MARKER_COLOR_LEVELS = 64
MARKER_COLOR_MIN = 48
MARKER_COLOR_STEP = 3
MARKER_COLOR_CAPACITY = MARKER_COLOR_LEVELS ** 3


@dataclass(frozen=True)
class MarkerSpec:
    marker_id: str
    source_id: str
    tex_path: str
    kind: str
    env: str
    delimiter: str
    raw_latex: str
    canonical_latex: str
    color_hex: str
    color_int: int
    char_start: int
    char_end: int
    file_char_start: int
    file_char_end: int
    macro_expansion_warnings: tuple[str, ...]
    source_offset_status: str
    source_offset_warnings: tuple[str, ...]


@dataclass
class ColorCapture:
    page_num: int | None = None
    glyphs: list[dict[str, Any]] | None = None
    vectors: list[dict[str, Any]] | None = None
    fonts: Counter[str] | None = None
    pages: set[int] | None = None

    def __post_init__(self) -> None:
        if self.glyphs is None:
            self.glyphs = []
        if self.vectors is None:
            self.vectors = []
        if self.fonts is None:
            self.fonts = Counter()
        if self.pages is None:
            self.pages = set()
        if self.page_num is not None:
            self.pages.add(int(self.page_num))


def build_instrumented_dataset(
    *,
    case_name: str,
    output_dir: Path,
    custom_pdf: Path | None = None,
    custom_latex_root: Path | None = None,
    start_page: int = 0,
    max_pages: int = 0,
    match_scope: str = "all",
    limit: int = 0,
    main_tex: Path | None = None,
    build_profile: str = "full",
    compile_mode: str = "latexmk",
    keep_work_dir: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    _log(
        "start instrumented dataset "
        f"case={case_name} output_dir={output_dir} profile={build_profile} "
        f"limit={limit} match_scope={match_scope}"
    )
    all_source_rows: list[dict[str, Any]] = []
    all_box_rows: list[dict[str, Any]] = []
    all_training_rows: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []
    marker_color_offset = 0

    for case in _cases_for_build(case_name, custom_pdf=custom_pdf, custom_latex_root=custom_latex_root):
        case_started = time.perf_counter()
        _log(f"[{case.name}] scan LaTeX source")
        source_records, display_count, inline_count = _instrumentable_source_index(
            case,
            max_pages=max_pages,
            match_scope=match_scope,
            start_page=start_page,
        )
        if limit > 0:
            source_records = source_records[:limit]
        _log(
            f"[{case.name}] source formulas selected={len(source_records)} "
            f"display_total={display_count} inline_total={inline_count}"
        )
        work_root = output_dir / "work" / case.name / "source"
        build_dir = output_dir / "work" / case.name / "build"
        _reset_dir(work_root)
        _reset_dir(build_dir)
        _log(f"[{case.name}] copy source tree to {work_root}")
        _copy_source_tree(case.latex_root, work_root)
        main = _main_tex_for_copy(case.latex_root, work_root, main_tex)
        _log(f"[{case.name}] apply build profile={build_profile} main_tex={main}")
        _apply_build_profile(work_root, build_profile, main)
        _log(f"[{case.name}] instrument formula spans")
        markers = _instrument_source_tree(case.latex_root, work_root, source_records, marker_index_offset=marker_color_offset)
        marker_color_offset += len(markers)
        _ensure_unique_marker_colors(markers)
        _ensure_marker_preamble(main)
        _log(f"[{case.name}] compile instrumented PDF mode={compile_mode}")
        compiled_pdf, compile_info = _compile_instrumented_pdf(work_root, build_dir, main, compile_mode=compile_mode)
        _log(
            f"[{case.name}] compiled pdf={compiled_pdf} "
            f"exit_code={compile_info['exit_code']}"
        )
        pdf_alignment = _pdf_alignment(case.pdf, compiled_pdf) if case.pdf.exists() else {"status": "missing_reference_pdf"}
        _log(f"[{case.name}] extract colored PDF structure boxes")
        captures = _extract_color_captures(compiled_pdf, markers)
        box_rows = [
            _box_row(
                case_name=case.name,
                case_pdf=case.pdf,
                compiled_pdf=compiled_pdf,
                marker=marker,
                capture=captures.get(marker.color_int),
                reference_pdf_alignment_status=str(pdf_alignment["status"]),
            )
            for marker in markers
        ]
        training_rows = [_training_row(row) for row in box_rows if row.get("verified_exact_box") is True]
        source_rows = [{"case": case.name, **asdict(record)} for record in source_records]
        all_source_rows.extend(source_rows)
        all_box_rows.extend(box_rows)
        all_training_rows.extend(training_rows)
        _log(
            f"[{case.name}] boxes found={sum(1 for row in box_rows if row.get('box_status') == 'found')} "
            f"verified={sum(1 for row in box_rows if row.get('verified_exact_box') is True)} "
            f"training_rows={len(training_rows)}"
        )
        if not keep_work_dir:
            _remove_dir(output_dir / "work" / case.name / "source")
        case_summaries.append(
            {
                "case": case.name,
                "elapsed_sec": round(time.perf_counter() - case_started, 3),
                "target_pdf": str(case.pdf),
                "compiled_pdf": str(compiled_pdf),
                "reference_pdf_alignment_status": pdf_alignment["status"],
                "compile_exit_code": compile_info["exit_code"],
                "compile_forced_pdf": compile_info["exit_code"] != 0,
                "build_profile": build_profile,
                "compile_mode": compile_mode,
                "source_formulas": len(source_records),
                "source_display_formulas": display_count,
                "source_inline_formulas": inline_count,
                "markers": len(markers),
                "marker_colors": len({marker.color_int for marker in markers}),
                "marker_color_unique": True,
                "boxes_found": sum(1 for row in box_rows if row.get("box_status") == "found"),
                "verified_exact_boxes": sum(1 for row in box_rows if row.get("verified_exact_box") is True),
                "training_rows": len(training_rows),
                "blockers": _blocker_counts(box_rows),
            }
        )

    _write_jsonl(output_dir / "source_formulas.jsonl", all_source_rows)
    _write_jsonl(output_dir / "instrumented_formula_boxes.jsonl", all_box_rows)
    _write_jsonl(output_dir / "instrumented_training_rows.jsonl", all_training_rows)
    _ensure_unique_marker_colors([
        MarkerSpec(
            marker_id=str(row.get("marker_id", "")),
            source_id=str(row.get("source_id", "")),
            tex_path=str(row.get("tex_path", "")),
            kind=str(row.get("kind", "")),
            env=str(row.get("env", "")),
            delimiter=str(row.get("delimiter", "")),
            raw_latex=str(row.get("raw_latex", "")),
            canonical_latex=str(row.get("canonical_latex", "")),
            color_hex=str(row.get("color_hex", "")),
            color_int=int(row.get("color_int", 0) or 0),
            char_start=int(row.get("char_start", 0) or 0),
            char_end=int(row.get("char_end", 0) or 0),
            file_char_start=int(row.get("file_char_start", 0) or 0),
            file_char_end=int(row.get("file_char_end", 0) or 0),
            macro_expansion_warnings=tuple(),
            source_offset_status="exact_offset",
            source_offset_warnings=tuple(),
        )
        for row in all_box_rows
    ])
    summary = {
        "schema_version": SCHEMA_VERSION,
        "case": case_name,
        "output_dir": str(output_dir),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "start_page": start_page,
        "max_pages": max_pages,
        "match_scope": match_scope,
        "limit": limit,
        "build_profile": build_profile,
        "compile_mode": compile_mode,
        "cases": case_summaries,
        "totals": {
            "source_formulas": len(all_source_rows),
            "formula_boxes": len(all_box_rows),
            "boxes_found": sum(1 for row in all_box_rows if row.get("box_status") == "found"),
            "verified_exact_boxes": sum(1 for row in all_box_rows if row.get("verified_exact_box") is True),
            "training_rows": len(all_training_rows),
            "blockers": _blocker_counts(all_box_rows),
        },
        "notes": [
            "This route uses LaTeX source only to manufacture audit/training labels.",
            "Color instrumentation is applied in a temporary copy and does not modify the user's source tree.",
            "The compiled instrumented PDF is the coordinate baseline for this dataset.",
            "A row is exact only after unique marker color and capture component quality checks pass.",
            "The user-supplied PDF is only an optional reference fingerprint and never blocks training rows.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    _log(f"done instrumented dataset summary={output_dir / 'summary.json'}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def _cases_for_build(case_name: str, *, custom_pdf: Path | None, custom_latex_root: Path | None) -> list[Any]:
    if custom_pdf is None and custom_latex_root is None:
        return _select_cases(case_name)
    if custom_pdf is None or custom_latex_root is None:
        raise ValueError("--pdf and --latex-root must be provided together")
    if case_name == "all":
        raise ValueError("--case must be concrete when --pdf/--latex-root are provided")
    return [custom_case(case_name, custom_pdf, custom_latex_root)]


def _instrumentable_source_index(
    case: Any,
    *,
    max_pages: int,
    match_scope: str,
    start_page: int,
) -> tuple[list[SourceFormulaRecord], int, int]:
    source_page_limit = max(0, int(start_page)) + max(0, int(max_pages)) if max_pages > 0 else 0
    macro_expander = load_latex_macro_expander(case.latex_root)
    entries = _ordered_source_entries(case.latex_root)
    page_windows = _entry_page_windows(entries, max_pages=source_page_limit)
    records: list[SourceFormulaRecord] = []
    display_count = 0
    inline_count = 0
    for entry in entries:
        try:
            text = entry.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        window_start, window_end = page_windows.get(int(entry.order), (None, None))
        for span in extract_latex_math_spans(text):
            if span.kind == "display":
                display_count += 1
            else:
                inline_count += 1
            if match_scope != "all" and span.kind != match_scope:
                continue
            expansion = macro_expander.expand(span.body)
            canonical_latex = expansion.latex or span.body.strip()
            normalized = _normalize_formula_for_match(canonical_latex)
            records.append(
                SourceFormulaRecord(
                    source_id="",
                    kind=span.kind,
                    latex=span.body.strip(),
                    canonical_latex=canonical_latex,
                    normalized=normalized,
                    token_count=len(_match_tokens(normalized)),
                    tex_path=str(entry.path.relative_to(case.latex_root)),
                    tex_order=int(entry.order),
                    char_start=int(span.body_start),
                    char_end=int(span.body_end),
                    env=span.env,
                    delimiter=span.delimiter,
                    parser_version="latex_math_source_parser_v1_file_offsets",
                    macro_expansion_version=expansion.version,
                    macro_expansion_applied=expansion.applied_macros,
                    macro_expansion_warnings=expansion.warnings,
                    pdf_page_hint=window_start,
                    pdf_page_window_start=window_start,
                    pdf_page_window_end=window_end,
                    page_alignment_source="pdf_toc_effective_page" if window_start is not None else "",
                    context_before=" ".join(text[max(0, span.body_start - 180) : span.body_start].split())[-180:],
                    context_after=" ".join(text[span.body_end : span.body_end + 180].split())[:180],
                )
            )
    records = sorted(records, key=lambda item: (item.tex_order, item.char_start, item.char_end))
    records = [
        SourceFormulaRecord(
            source_id=f"src_{index:06d}",
            kind=record.kind,
            latex=record.latex,
            canonical_latex=record.canonical_latex,
            normalized=record.normalized,
            token_count=record.token_count,
            tex_path=record.tex_path,
            tex_order=record.tex_order,
            char_start=record.char_start,
            char_end=record.char_end,
            env=record.env,
            context_before=record.context_before,
            context_after=record.context_after,
            delimiter=record.delimiter,
            parser_version=record.parser_version,
            macro_expansion_version=record.macro_expansion_version,
            macro_expansion_applied=record.macro_expansion_applied,
            macro_expansion_warnings=record.macro_expansion_warnings,
            pdf_page_hint=record.pdf_page_hint,
            pdf_page_window_start=record.pdf_page_window_start,
            pdf_page_window_end=record.pdf_page_window_end,
            page_alignment_source=record.page_alignment_source,
        )
        for index, record in enumerate(records)
    ]
    return records, display_count, inline_count


def _match_tokens(normalized_formula: str) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[a-z0-9]+|\\[a-z]+|[+\-*/^_=<>]+", str(normalized_formula or ""))
        if token
    }


def _copy_source_tree(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns(
        ".git",
        ".svn",
        "__pycache__",
        "build",
        "$out",
        "*.aux",
        "*.bbl",
        "*.blg",
        "*.fls",
        "*.fdb_latexmk",
        "*.log",
        "*.out",
        "*.synctex",
        "*.synctex.gz",
        "*.toc",
    )
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=ignore)


def _apply_build_profile(work_root: Path, build_profile: str, main_tex: Path) -> None:
    profile = (build_profile or "full").strip().lower()
    if profile == "full":
        return
    if profile != "fast-no-asy":
        raise ValueError(f"unknown build profile: {build_profile}")
    _patch_latexmkrc_skip_asy(work_root)
    _patch_asy_package_skip_graphics(work_root)
    _ensure_no_asy_preamble(main_tex)


def _patch_latexmkrc_skip_asy(work_root: Path) -> None:
    latexmkrc = work_root / ".latexmkrc"
    marker = "PDF_AI_READER_FAST_NO_ASY_PROFILE"
    snippet = f"""

# {marker}: dataset build profile.  Only affects this temporary source copy.
sub asy {{
  print "pdf_ai_reader: skip Asymptote dependency $_[0]\\n";
  return 0;
}}
$max_repeat = 4 if !$max_repeat || $max_repeat > 4;
"""
    _append_once(latexmkrc, marker, snippet)


def _patch_asy_package_skip_graphics(work_root: Path) -> None:
    marker = "PDF_AI_READER_FAST_NO_ASY_PROFILE"
    snippet = r"""

% PDF_AI_READER_FAST_NO_ASY_PROFILE: dataset build profile.
% This temporary-copy shim consumes Asymptote bodies without rendering figures.
\makeatletter
\@ifundefined{ProcessAsymptote}{}{%
  \renewcommand\asy[1][]{%
    \begingroup
    \let\ThisAsymptote\@gobble
    \ProcessAsymptote{asy}%
  }%
  \def\endasy{\endgroup}%
  \def\asydef{%
    \begingroup
    \let\ThisAsymptote\@gobble
    \ProcessAsymptote{asydef}%
  }%
  \def\endasydef{\endgroup}%
  \renewcommand\asyinclude[2][]{}
}
\makeatother
"""
    for sty in sorted(work_root.rglob("*asy*.sty")):
        _append_once(sty, marker, snippet)


def _ensure_no_asy_preamble(main_tex: Path) -> None:
    marker = "PDF_AI_READER_FAST_NO_ASY_PROFILE"
    text = main_tex.read_text(encoding="utf-8", errors="ignore")
    if marker in text:
        return
    shim = r"""
% PDF_AI_READER_FAST_NO_ASY_PROFILE: dataset build profile.
\makeatletter
\@ifundefined{ProcessAsymptote}{}{%
  \renewcommand\asy[1][]{%
    \begingroup
    \let\ThisAsymptote\@gobble
    \ProcessAsymptote{asy}%
  }%
  \def\endasy{\endgroup}%
  \def\asydef{%
    \begingroup
    \let\ThisAsymptote\@gobble
    \ProcessAsymptote{asydef}%
  }%
  \def\endasydef{\endgroup}%
  \renewcommand\asyinclude[2][]{}
}
\makeatother
"""
    begin = text.find(r"\begin{document}")
    if begin >= 0:
        text = text[:begin] + shim + "\n" + text[begin:]
    else:
        text = text + "\n" + shim
    main_tex.write_text(text, encoding="utf-8")


def _append_once(path: Path, marker: str, snippet: str) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if marker in text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + snippet + "\n", encoding="utf-8")


def _main_tex_for_copy(original_root: Path, work_root: Path, main_tex: Path | None) -> Path:
    if main_tex is not None:
        original = main_tex if main_tex.is_absolute() else original_root / main_tex
        relative = original.resolve().relative_to(original_root.resolve())
        return work_root / relative
    root_tex = sorted(path for path in work_root.glob("*.tex") if path.is_file())
    preferred = [path for path in root_tex if path.stem.lower() in {"main", "paper", "ms", "napkin"}]
    if preferred:
        return sorted(preferred, key=lambda p: (p.stem.lower() != "main", p.name.lower()))[0]
    if len(root_tex) == 1:
        return root_tex[0]
    if root_tex:
        return root_tex[0]
    raise RuntimeError(f"no main .tex file found in {work_root}")


def _instrument_source_tree(
    original_root: Path,
    work_root: Path,
    records: list[SourceFormulaRecord],
    *,
    marker_index_offset: int = 0,
) -> list[MarkerSpec]:
    by_file: dict[str, list[tuple[SourceFormulaRecord, MarkerSpec]]] = defaultdict(list)
    markers: list[MarkerSpec] = []
    for index, record in enumerate(records):
        marker = _marker_for_record(original_root, record, index, color_index=marker_index_offset + index)
        markers.append(marker)
        if marker.source_offset_status in {"exact_offset", "unique_raw_latex_relocated"}:
            by_file[record.tex_path].append((record, marker))
    for tex_path, items in by_file.items():
        path = work_root / tex_path
        text = path.read_text(encoding="utf-8", errors="ignore")
        for record, marker in sorted(items, key=lambda item: item[0].char_start, reverse=True):
            body = text[marker.file_char_start : marker.file_char_end]
            text = (
                text[: marker.file_char_start]
                + _instrumented_body(record, marker.color_hex, body)
                + text[marker.file_char_end :]
            )
        path.write_text(text, encoding="utf-8")
    return markers


def _marker_for_record(original_root: Path, record: SourceFormulaRecord, index: int, *, color_index: int | None = None) -> MarkerSpec:
    color_hex = _marker_color(index if color_index is None else color_index)
    status = "exact_offset"
    warnings: list[str] = []
    file_start = int(record.char_start)
    file_end = int(record.char_end)
    path = original_root / record.tex_path
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        actual = text[record.char_start : record.char_end].strip()
        if actual != record.latex.strip():
            relocated = _relocate_raw_latex(text, record)
            if relocated is None:
                status = "offset_mismatch"
                warnings.append("source_offset_text_mismatch")
            else:
                file_start, file_end = relocated
                status = "unique_raw_latex_relocated"
                warnings.append("source_offset_relocated_by_unique_raw_latex")
    except OSError as exc:
        status = "source_file_unreadable"
        warnings.append(str(exc))
    return MarkerSpec(
        marker_id=f"m{index:06d}",
        source_id=record.source_id,
        tex_path=record.tex_path,
        kind=record.kind,
        env=record.env,
        delimiter=record.delimiter,
        raw_latex=record.latex,
        canonical_latex=record.canonical_latex,
        color_hex=color_hex,
        color_int=int(color_hex, 16),
        char_start=record.char_start,
        char_end=record.char_end,
        file_char_start=file_start,
        file_char_end=file_end,
        macro_expansion_warnings=record.macro_expansion_warnings,
        source_offset_status=status,
        source_offset_warnings=tuple(warnings),
    )


def _relocate_raw_latex(text: str, record: SourceFormulaRecord) -> tuple[int, int] | None:
    needle = str(record.latex or "")
    if not needle:
        return None
    matches: list[int] = []
    start = text.find(needle)
    while start >= 0:
        matches.append(start)
        start = text.find(needle, start + 1)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0], matches[0] + len(needle)
    preferred = _body_start_offset(text)
    target = preferred + int(record.char_start)
    best = min(matches, key=lambda value: abs(value - target))
    if sum(1 for value in matches if abs(value - target) == abs(best - target)) == 1:
        return best, best + len(needle)
    return None


def _body_start_offset(text: str) -> int:
    marker = r"\begin{document}"
    index = text.find(marker)
    return index + len(marker) if index >= 0 else 0


def _marker_color(index: int) -> str:
    """Return a deterministic marker color with no collisions under capacity."""

    if index < 0 or index >= MARKER_COLOR_CAPACITY:
        raise ValueError(f"marker color index out of range: {index}")
    shuffled = ((int(index) + 1) * 1103515245 + 12345) % MARKER_COLOR_CAPACITY
    r_digit = shuffled & 0x3F
    g_digit = (shuffled >> 6) & 0x3F
    b_digit = (shuffled >> 12) & 0x3F
    r = MARKER_COLOR_MIN + MARKER_COLOR_STEP * r_digit
    g = MARKER_COLOR_MIN + MARKER_COLOR_STEP * g_digit
    b = MARKER_COLOR_MIN + MARKER_COLOR_STEP * b_digit
    return f"{r:02X}{g:02X}{b:02X}"


def _ensure_unique_marker_colors(markers: list[MarkerSpec]) -> None:
    by_color: dict[int, list[str]] = defaultdict(list)
    for marker in markers:
        by_color[int(marker.color_int)].append(marker.marker_id)
    duplicates = {f"{color:06X}": ids for color, ids in by_color.items() if len(ids) > 1}
    if duplicates:
        sample = dict(list(sorted(duplicates.items()))[:8])
        raise RuntimeError(f"marker color collision detected: {sample}")


def _instrumented_body(record: SourceFormulaRecord, color_hex: str, body: str) -> str:
    if record.delimiter == "environment":
        if record.env.startswith(("align", "gather", "multline", "flalign", "eqnarray", "IEEEeqnarray")):
            return _color_each_display_row(body, color_hex)
        return rf"{MARKER_COMMAND}{{{color_hex}}}{{{body}}}"
    return rf"{MARKER_COMMAND}{{{color_hex}}}{{{body}}}"


def _color_each_display_row(body: str, color_hex: str) -> str:
    color = rf"\color[HTML]{{{color_hex}}}"
    parts = body.split(r"\\")
    colored = []
    for part in parts:
        colored.append(_color_alignment_cells(part, color))
    return r"\\".join(colored)


def _color_alignment_cells(row: str, color_command: str) -> str:
    cells = _split_unescaped_alignment_cells(row)
    colored: list[str] = []
    for cell, separator in cells:
        stripped = cell.lstrip()
        prefix_len = len(cell) - len(stripped)
        if stripped:
            colored.append(cell[:prefix_len] + color_command + " " + stripped)
        else:
            colored.append(cell)
        colored.append(separator)
    return "".join(colored)


def _split_unescaped_alignment_cells(row: str) -> list[tuple[str, str]]:
    cells: list[tuple[str, str]] = []
    start = 0
    index = 0
    while index < len(row):
        if row[index] == "&" and not _is_escaped_latex_char(row, index):
            cells.append((row[start:index], "&"))
            start = index + 1
        index += 1
    cells.append((row[start:], ""))
    return cells


def _is_escaped_latex_char(text: str, index: int) -> bool:
    count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        count += 1
        cursor -= 1
    return count % 2 == 1


def _ensure_marker_preamble(main_tex: Path) -> None:
    text = main_tex.read_text(encoding="utf-8", errors="ignore")
    if MARKER_COMMAND in text:
        return
    package_line = "" if _loads_color_package(text) else "\\usepackage{xcolor}\n"
    insert = package_line + MARKER_PREAMBLE + "\n"
    begin = text.find(r"\begin{document}")
    if begin >= 0:
        text = text[:begin] + insert + text[begin:]
    else:
        text = insert + text
    main_tex.write_text(text, encoding="utf-8")


def _loads_color_package(text: str) -> bool:
    compact = text.replace(" ", "")
    return r"\usepackage{xcolor}" in compact or r"\usepackage{color}" in compact


def _compile_instrumented_pdf(
    work_root: Path,
    build_dir: Path,
    main_tex: Path,
    *,
    compile_mode: str,
) -> tuple[Path, dict[str, Any]]:
    mode = (compile_mode or "latexmk").strip().lower()
    if mode == "pdflatex-once":
        return _compile_with_pdflatex_once(work_root, build_dir, main_tex)
    if mode != "latexmk":
        raise ValueError(f"unknown compile mode: {compile_mode}")
    latexmk = shutil.which("latexmk")
    if not latexmk:
        raise RuntimeError("latexmk not found on PATH")
    build_dir.mkdir(parents=True, exist_ok=True)
    _mirror_source_dirs(work_root, build_dir)
    main_arg = main_tex.resolve().relative_to(work_root.resolve()).as_posix()
    out_arg = build_dir.resolve().as_posix()
    args = [
        latexmk,
        "-f",
        "-pdf",
        "-synctex=1",
        "-interaction=nonstopmode",
        f"-outdir={out_arg}",
        main_arg,
    ]
    compile_log = build_dir / f"{main_tex.stem}.latexmk.stdout.log"
    _log(f"latexmk output log={compile_log}")
    with compile_log.open("wb") as handle:
        result = subprocess.run(
            args,
            cwd=str(work_root),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    tail = _tail_text(compile_log, max_lines=80)
    info = {
        "exit_code": result.returncode,
        "stdout_log": str(compile_log),
        "tail": tail,
    }
    pdf = build_dir / f"{main_tex.stem}.pdf"
    if not pdf.exists():
        pdfs = sorted(build_dir.glob("*.pdf"))
        if not pdfs and result.returncode != 0:
            raise RuntimeError(
                f"instrumented latexmk failed with exit code {result.returncode}; "
                f"see {compile_log}\n{info['tail']}"
            )
        if not pdfs:
            raise RuntimeError(f"latexmk succeeded but no PDF found in {build_dir}")
        pdf = pdfs[0]
    return pdf, info


def _compile_with_pdflatex_once(work_root: Path, build_dir: Path, main_tex: Path) -> tuple[Path, dict[str, Any]]:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        raise RuntimeError("pdflatex not found on PATH")
    build_dir.mkdir(parents=True, exist_ok=True)
    _mirror_source_dirs(work_root, build_dir)
    main_arg = main_tex.resolve().relative_to(work_root.resolve()).as_posix()
    out_arg = build_dir.resolve().as_posix()
    args = [
        pdflatex,
        "-synctex=1",
        "-interaction=nonstopmode",
        "-recorder",
        f"-output-directory={out_arg}",
        main_arg,
    ]
    compile_log = build_dir / f"{main_tex.stem}.pdflatex-once.stdout.log"
    _log(f"pdflatex output log={compile_log}")
    with compile_log.open("wb") as handle:
        result = subprocess.run(
            args,
            cwd=str(work_root),
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    tail = _tail_text(compile_log, max_lines=80)
    pdf = build_dir / f"{main_tex.stem}.pdf"
    info = {
        "exit_code": result.returncode,
        "stdout_log": str(compile_log),
        "tail": tail,
    }
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise RuntimeError(
            f"instrumented pdflatex-once failed with exit code {result.returncode}; "
            f"see {compile_log}\n{tail}"
        )
    return pdf, info


def _tail_text(path: Path, *, max_lines: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"<failed to read {path}: {exc}>"
    return "\n".join(lines[-max_lines:])


def _mirror_source_dirs(work_root: Path, build_dir: Path) -> None:
    for path in work_root.rglob("*"):
        if not path.is_dir():
            continue
        relative = path.relative_to(work_root)
        if not relative.parts:
            continue
        (build_dir / relative).mkdir(parents=True, exist_ok=True)


def _extract_color_captures(pdf: Path, markers: list[MarkerSpec]) -> dict[int, ColorCapture]:
    wanted = {marker.color_int for marker in markers}
    captures: dict[int, ColorCapture] = {}
    doc = fitz.open(pdf)
    try:
        for page_num in range(doc.page_count):
            page = doc[page_num]
            _collect_colored_glyphs(page, page_num, wanted, captures)
            _collect_colored_vectors(page, page_num, wanted, captures)
    finally:
        doc.close()
    return captures


def _collect_colored_glyphs(
    page: fitz.Page,
    page_num: int,
    wanted: set[int],
    captures: dict[int, ColorCapture],
) -> None:
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                color = _color_int(span.get("color"))
                if color not in wanted:
                    continue
                capture = captures.setdefault(color, ColorCapture(page_num=page_num))
                if capture.page_num is None:
                    capture.page_num = page_num
                capture.pages.add(page_num)
                font = str(span.get("font", ""))
                size = _float(span.get("size"))
                for char in span.get("chars", []):
                    bbox = char.get("bbox")
                    if not _valid_bbox(bbox):
                        continue
                    capture.fonts[font] += 1
                    capture.glyphs.append(
                        {
                            "text": str(char.get("c", "")),
                            "font": font,
                            "size": round(size, 6),
                            "page_num": page_num,
                            "bbox": [round(float(value), 3) for value in bbox],
                        }
                    )


def _collect_colored_vectors(
    page: fitz.Page,
    page_num: int,
    wanted: set[int],
    captures: dict[int, ColorCapture],
) -> None:
    try:
        drawings = page.get_drawings()
    except Exception:
        return
    for drawing in drawings:
        colors = {_tuple_color_to_int(drawing.get("color")), _tuple_color_to_int(drawing.get("fill"))}
        matched = [color for color in colors if color in wanted]
        if not matched:
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        bbox = [round(float(rect.x0), 3), round(float(rect.y0), 3), round(float(rect.x1), 3), round(float(rect.y1), 3)]
        for color in matched:
            capture = captures.setdefault(color, ColorCapture(page_num=page_num))
            if capture.page_num is None:
                capture.page_num = page_num
            capture.pages.add(page_num)
            capture.vectors.append({"bbox": bbox, "page_num": page_num, "type": str(drawing.get("type", ""))})


def _box_row(
    *,
    case_name: str,
    case_pdf: Path,
    compiled_pdf: Path,
    marker: MarkerSpec,
    capture: ColorCapture | None,
    reference_pdf_alignment_status: str,
) -> dict[str, Any]:
    glyphs = list(capture.glyphs if capture else [])
    vectors = list(capture.vectors if capture else [])
    bbox = _union_bbox([item["bbox"] for item in glyphs] + [item["bbox"] for item in vectors])
    components = _capture_components(glyphs, vectors)
    pages = sorted(capture.pages if capture and capture.pages else [])
    page_bboxes = _page_bboxes(glyphs + vectors)
    blockers = _box_blockers(marker, capture, bbox, components)
    warnings = _box_warnings(marker, components, pages)
    return {
        "schema_version": BOX_SCHEMA_VERSION,
        "case": case_name,
        "source_id": marker.source_id,
        "marker_id": marker.marker_id,
        "tex_path": marker.tex_path,
        "kind": marker.kind,
        "env": marker.env,
        "delimiter": marker.delimiter,
        "raw_latex": marker.raw_latex,
        "canonical_latex": marker.canonical_latex,
        "label_latex": marker.canonical_latex,
        "color_hex": marker.color_hex,
        "color_int": marker.color_int,
        "char_start": marker.char_start,
        "char_end": marker.char_end,
        "file_char_start": marker.file_char_start,
        "file_char_end": marker.file_char_end,
        "source_offset_status": marker.source_offset_status,
        "source_offset_warnings": list(marker.source_offset_warnings),
        "macro_expansion_warnings": list(marker.macro_expansion_warnings),
        "target_pdf": str(case_pdf),
        "compiled_pdf": str(compiled_pdf),
        "reference_pdf_alignment_status": reference_pdf_alignment_status,
        "coordinate_baseline": "compiled_instrumented_pdf",
        "capture_quality_version": CAPTURE_QUALITY_VERSION,
        "box_status": "found" if bbox is not None else "missing",
        "page_num": capture.page_num if capture else None,
        "capture_pages": pages,
        "page_bboxes": page_bboxes,
        "bbox": bbox,
        "glyph_count": len(glyphs),
        "vector_count": len(vectors),
        "capture_component_count": len(components),
        "capture_components": components,
        "fonts": dict(sorted((capture.fonts or Counter()).items())) if capture else {},
        "text_sample": "".join(item["text"] for item in glyphs)[:500],
        "sampled_glyphs": glyphs,
        "sampled_vectors": vectors,
        "verified_exact_box": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def _box_blockers(
    marker: MarkerSpec,
    capture: ColorCapture | None,
    bbox: list[float] | None,
    components: list[dict[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if marker.source_offset_status not in {"exact_offset", "unique_raw_latex_relocated"}:
        blockers.append("source_offset_not_verified")
    if marker.macro_expansion_warnings:
        blockers.append("macro_expansion_warnings_present")
    if capture is None or bbox is None:
        blockers.append("marker_color_not_found_in_pdf")
    elif not capture.glyphs and not capture.vectors:
        blockers.append("empty_marker_capture")
    else:
        if capture.glyphs and not components:
            blockers.append("empty_nonspace_marker_capture")
    return blockers


def _box_warnings(marker: MarkerSpec, components: list[dict[str, Any]], pages: list[int]) -> list[str]:
    warnings: list[str] = []
    if len(pages) > 1:
        warnings.append("marker_color_seen_on_multiple_pages")
    if marker.kind == "inline" and len(components) > 1:
        warnings.append("inline_disconnected_marker_capture")
    return warnings


def _training_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": TRAINING_ROW_SCHEMA_VERSION,
        "case": row.get("case", ""),
        "source_id": row.get("source_id", ""),
        "marker_id": row.get("marker_id", ""),
        "kind": row.get("kind", ""),
        "label_latex": row.get("label_latex", ""),
        "raw_source_latex": row.get("raw_latex", ""),
        "label_source": "latex_source_macro_expanded",
        "pdf_window_source": "instrumented_latex_color",
        "compiled_pdf": row.get("compiled_pdf", ""),
        "target_pdf": row.get("target_pdf", ""),
        "coordinate_baseline": "compiled_instrumented_pdf",
        "capture_quality_version": row.get("capture_quality_version", ""),
        "page_num": row.get("page_num"),
        "capture_pages": row.get("capture_pages", []),
        "page_bboxes": row.get("page_bboxes", []),
        "bbox": row.get("bbox"),
        "tex_path": row.get("tex_path", ""),
        "glyph_count": row.get("glyph_count", 0),
        "vector_count": row.get("vector_count", 0),
        "capture_component_count": row.get("capture_component_count", 0),
        "capture_components": row.get("capture_components", []),
        "fonts": row.get("fonts", {}),
        "text_sample": row.get("text_sample", ""),
        "sampled_glyphs": row.get("sampled_glyphs", []),
        "sampled_vectors": row.get("sampled_vectors", []),
        "verified_exact_box": row.get("verified_exact_box", False),
        "blockers": row.get("blockers", []),
        "warnings": row.get("warnings", []),
    }


def _capture_components(glyphs: list[dict[str, Any]], vectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, glyph in enumerate(glyphs):
        text = str(glyph.get("text", "") or "")
        if not text.strip():
            continue
        bbox = glyph.get("bbox")
        if _valid_bbox(bbox):
            items.append({
                "kind": "glyph",
                "index": index,
                "page_num": _optional_int(glyph.get("page_num")),
                "bbox": [float(value) for value in bbox],
                "text": text,
            })
    for index, vector in enumerate(vectors):
        bbox = vector.get("bbox")
        if _valid_bbox(bbox):
            items.append({
                "kind": "vector",
                "index": index,
                "page_num": _optional_int(vector.get("page_num")),
                "bbox": [float(value) for value in bbox],
                "text": "",
            })
    if not items:
        return []
    heights = [max(0.0, item["bbox"][3] - item["bbox"][1]) for item in items]
    widths = [max(0.0, item["bbox"][2] - item["bbox"][0]) for item in items]
    median_height = max(_median([value for value in heights if value > 0.0]), 1.0)
    median_width = max(_median([value for value in widths if value > 0.0]), 1.0)
    x_margin = max(1.0, median_width * 2.0, median_height * 1.2)
    y_margin = max(1.0, median_height * 1.6)
    parents = list(range(len(items)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(items)):
        for right in range(left + 1, len(items)):
            if items[left].get("page_num") != items[right].get("page_num"):
                continue
            if _boxes_are_near(items[left]["bbox"], items[right]["bbox"], x_margin=x_margin, y_margin=y_margin):
                union(left, right)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(items):
        grouped[find(index)].append(item)
    components: list[dict[str, Any]] = []
    for component_items in grouped.values():
        boxes = [item["bbox"] for item in component_items]
        bbox = _union_bbox(boxes)
        components.append(
            {
                "page_num": component_items[0].get("page_num"),
                "bbox": bbox,
                "glyph_count": sum(1 for item in component_items if item["kind"] == "glyph"),
                "vector_count": sum(1 for item in component_items if item["kind"] == "vector"),
                "text_sample": "".join(item["text"] for item in sorted(component_items, key=lambda item: (item["bbox"][0], item["bbox"][1], item["index"])) if item["text"])[:120],
            }
        )
    return sorted(components, key=lambda item: (float((item.get("bbox") or [0, 0, 0, 0])[1]), float((item.get("bbox") or [0, 0, 0, 0])[0])))


def _page_bboxes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[list[float]]] = defaultdict(list)
    for item in items:
        page_num = _optional_int(item.get("page_num"))
        bbox = item.get("bbox")
        if page_num is None or not _valid_bbox(bbox):
            continue
        grouped[page_num].append([float(value) for value in bbox])
    return [
        {"page_num": page_num, "bbox": _union_bbox(boxes)}
        for page_num, boxes in sorted(grouped.items())
        if boxes
    ]


def _boxes_are_near(
    left: list[float],
    right: list[float],
    *,
    x_margin: float,
    y_margin: float,
) -> bool:
    return not (
        left[2] + x_margin < right[0]
        or right[2] + x_margin < left[0]
        or left[3] + y_margin < right[1]
        or right[3] + y_margin < left[1]
    )


def _union_bbox(boxes: list[list[float]]) -> list[float] | None:
    clean = [box for box in boxes if _valid_bbox(box)]
    if not clean:
        return None
    return [
        round(min(float(box[0]) for box in clean), 3),
        round(min(float(box[1]) for box in clean), 3),
        round(max(float(box[2]) for box in clean), 3),
        round(max(float(box[3]) for box in clean), 3),
    ]


def _color_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _tuple_color_to_int(value: Any) -> int:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return -1
    try:
        r = max(0, min(255, round(float(value[0]) * 255)))
        g = max(0, min(255, round(float(value[1]) * 255)))
        b = max(0, min(255, round(float(value[2]) * 255)))
    except (TypeError, ValueError):
        return -1
    return (r << 16) | (g << 8) | b


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _valid_bbox(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 4


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _blocker_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for blocker in row.get("blockers", []) or []:
            counts[str(blocker)] += 1
    return dict(sorted(counts.items()))


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _remove_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="attention")
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument("--latex-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--match-scope", choices=["all", "display", "inline"], default="all")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--main-tex", type=Path, default=None)
    parser.add_argument("--build-profile", choices=["full", "fast-no-asy"], default="full")
    parser.add_argument("--compile-mode", choices=["latexmk", "pdflatex-once"], default="latexmk")
    parser.add_argument("--keep-work-dir", action="store_true")
    args = parser.parse_args()
    build_instrumented_dataset(
        case_name=args.case,
        output_dir=args.output_dir,
        custom_pdf=args.pdf,
        custom_latex_root=args.latex_root,
        start_page=args.start_page,
        max_pages=args.max_pages,
        match_scope=args.match_scope,
        limit=args.limit,
        main_tex=args.main_tex,
        build_profile=args.build_profile,
        compile_mode=args.compile_mode,
        keep_work_dir=args.keep_work_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
