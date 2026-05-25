"""Build source-supervised TinyBDMath rows with SyncTeX anchors.

This is the non-image dataset path for PDFs that are known to come from the
matching LaTeX source tree.  It uses mature TeX tooling for the hard part:

1. extract math spans from the LaTeX source with stable offsets,
2. expand project macros into canonical LaTeX training targets,
3. ask SyncTeX for the PDF location of each source span,
4. verify the anchor against PDF text/glyph facts,
5. write reusable JSONL anchors and conservative line-window training rows.

The output is source/audit data only.  It is not used by production PDF parsing
when source files are unavailable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.infra.file_hash import compute_sha256
from tools.born_digital_formula_dataset import (
    SourceFormulaRecord,
    _select_cases,
    _source_index,
    custom_case,
)


SCHEMA_VERSION = "tinybdmath_synctex_dataset_v1"
ANCHOR_SCHEMA_VERSION = "tinybdmath_synctex_anchor_v1"
TRAINING_ROW_SCHEMA_VERSION = "tinybdmath_synctex_line_training_row_v1"


@dataclass(frozen=True)
class SynctexTarget:
    pdf: Path
    directory: Path
    synctex_file: Path
    source: str


@dataclass(frozen=True)
class SourceLocation:
    tex_path: str
    input_name: str
    line: int
    column: int
    char_start: int
    char_end: int
    offset_status: str
    offset_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SynctexResult:
    output: str
    page: int | None
    x: float | None
    y: float | None
    h: float | None
    v: float | None
    width: float | None
    height: float | None
    before: str = ""
    offset: int | None = None
    middle: str = ""
    after: str = ""


def build_synctex_dataset(
    *,
    case_name: str,
    output_dir: Path,
    custom_pdf: Path | None = None,
    custom_latex_root: Path | None = None,
    start_page: int = 0,
    max_pages: int = 0,
    match_scope: str = "all",
    limit: int = 0,
    workers: int = 4,
    synctex_pdf: Path | None = None,
    synctex_dir: Path | None = None,
    compile_synctex: bool = False,
    main_tex: Path | None = None,
    include_glyphs: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    all_sources: list[dict[str, Any]] = []
    all_anchors: list[dict[str, Any]] = []
    all_training_rows: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []

    for case in _cases_for_build(case_name, custom_pdf=custom_pdf, custom_latex_root=custom_latex_root):
        case_started = time.perf_counter()
        if compile_synctex:
            _compile_latex_with_synctex(case.latex_root, main_tex=main_tex)
        target = _discover_synctex_target(
            target_pdf=case.pdf,
            latex_root=case.latex_root,
            explicit_pdf=synctex_pdf,
            explicit_dir=synctex_dir,
        )
        pdf_alignment = _pdf_alignment(case.pdf, target.pdf)
        source_records, display_count, inline_count = _source_index(
            case,
            max_pages=max_pages,
            match_scope=match_scope,
            start_page=start_page,
        )
        if limit > 0:
            source_records = source_records[:limit]
        located = [_located_source(case.latex_root, record) for record in source_records]
        same_line_counts = Counter(_line_scope_key(case.name, loc) for loc in located)
        anchor_rows = _run_synctex_queries(
            case_name=case.name,
            latex_root=case.latex_root,
            target_pdf=case.pdf,
            synctex_target=target,
            pdf_alignment=pdf_alignment,
            records=source_records,
            locations=located,
            same_line_counts=same_line_counts,
            workers=workers,
            include_glyphs=include_glyphs,
        )
        training_rows = [
            _training_row(row)
            for row in anchor_rows
            if row.get("usable_line_training_row") is True
        ]
        source_rows = [{"case": case.name, **asdict(record)} for record in source_records]
        all_sources.extend(source_rows)
        all_anchors.extend(anchor_rows)
        all_training_rows.extend(training_rows)
        case_summaries.append(
            {
                "case": case.name,
                "elapsed_sec": round(time.perf_counter() - case_started, 3),
                "target_pdf": str(case.pdf),
                "synctex_pdf": str(target.pdf),
                "synctex_file": str(target.synctex_file),
                "synctex_source": target.source,
                "pdf_alignment_status": pdf_alignment["status"],
                "source_formulas": len(source_records),
                "source_display_formulas": display_count,
                "source_inline_formulas": inline_count,
                "anchors": len(anchor_rows),
                "anchors_with_result": sum(1 for row in anchor_rows if row.get("synctex_status") == "done"),
                "verified_source_anchors": sum(1 for row in anchor_rows if row.get("verified_source_anchor") is True),
                "target_pdf_transferable_anchors": sum(1 for row in anchor_rows if row.get("target_pdf_transferable") is True),
                "usable_line_training_rows": len(training_rows),
                "blockers": _blocker_counts(anchor_rows),
            }
        )

    _write_jsonl(output_dir / "source_formulas.jsonl", all_sources)
    _write_jsonl(output_dir / "synctex_anchors.jsonl", all_anchors)
    _write_jsonl(output_dir / "synctex_line_training_rows.jsonl", all_training_rows)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "case": case_name,
        "output_dir": str(output_dir),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "start_page": start_page,
        "max_pages": max_pages,
        "match_scope": match_scope,
        "limit": limit,
        "workers": max(1, int(workers)),
        "cases": case_summaries,
        "totals": {
            "source_formulas": len(all_sources),
            "anchors": len(all_anchors),
            "anchors_with_result": sum(1 for row in all_anchors if row.get("synctex_status") == "done"),
            "verified_source_anchors": sum(1 for row in all_anchors if row.get("verified_source_anchor") is True),
            "target_pdf_transferable_anchors": sum(1 for row in all_anchors if row.get("target_pdf_transferable") is True),
            "usable_line_training_rows": len(all_training_rows),
            "blockers": _blocker_counts(all_anchors),
        },
        "notes": [
            "LaTeX source is used only for dataset/audit labels, never for production parsing.",
            "SyncTeX anchors are line/box evidence; exact formula crops still require an unambiguous line window or later marker instrumentation/review.",
            "Rows with multiple source formulas on the same TeX line are deliberately excluded from automatic line-window training rows.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _cases_for_build(case_name: str, *, custom_pdf: Path | None, custom_latex_root: Path | None) -> list[Any]:
    if custom_pdf is None and custom_latex_root is None:
        return _select_cases(case_name)
    if custom_pdf is None or custom_latex_root is None:
        raise ValueError("--pdf and --latex-root must be provided together")
    if case_name == "all":
        raise ValueError("--case must be a concrete name when --pdf/--latex-root are provided")
    return [custom_case(case_name, custom_pdf, custom_latex_root)]


def _compile_latex_with_synctex(latex_root: Path, *, main_tex: Path | None) -> None:
    latexmk = shutil.which("latexmk")
    if not latexmk:
        raise RuntimeError("latexmk not found on PATH")
    main = main_tex or _discover_main_tex(latex_root)
    main_arg = _path_for_cwd(main, latex_root)
    args = [
        latexmk,
        "-pdf",
        "-synctex=1",
        "-interaction=nonstopmode",
        "-halt-on-error",
        main_arg,
    ]
    result = subprocess.run(
        args,
        cwd=str(latex_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        tail = "\n".join(result.stdout.splitlines()[-80:])
        raise RuntimeError(f"latexmk failed with exit code {result.returncode}\n{tail}")


def _discover_main_tex(latex_root: Path) -> Path:
    root_tex = sorted(path for path in latex_root.glob("*.tex") if path.is_file())
    if len(root_tex) == 1:
        return root_tex[0]
    preferred = [path for path in root_tex if path.stem.lower() in {"main", "paper", "ms", "napkin"}]
    if preferred:
        return sorted(preferred, key=lambda p: (p.stem.lower() != "main", p.name.lower()))[0]
    if root_tex:
        return root_tex[0]
    raise RuntimeError(f"no root .tex file found under {latex_root}")


def _discover_synctex_target(
    *,
    target_pdf: Path,
    latex_root: Path,
    explicit_pdf: Path | None,
    explicit_dir: Path | None,
) -> SynctexTarget:
    if explicit_pdf is not None:
        pdf = explicit_pdf.resolve()
        directory = explicit_dir.resolve() if explicit_dir is not None else pdf.parent
        synctex_file = _sidecar_synctex(pdf, directory)
        if synctex_file is None:
            raise RuntimeError(f"no .synctex(.gz) file found for {pdf} in {directory}")
        return SynctexTarget(pdf=pdf, directory=directory, synctex_file=synctex_file, source="explicit")

    target_sidecar = _sidecar_synctex(target_pdf, target_pdf.parent)
    if target_sidecar is not None:
        return SynctexTarget(
            pdf=target_pdf.resolve(),
            directory=target_pdf.parent.resolve(),
            synctex_file=target_sidecar.resolve(),
            source="target_pdf_sidecar",
        )

    candidates: list[tuple[int, Path, Path]] = []
    for synctex_file in list(latex_root.rglob("*.synctex.gz")) + list(latex_root.rglob("*.synctex")):
        pdf = _pdf_for_synctex_file(synctex_file)
        if pdf is None or not pdf.exists():
            continue
        score = 0
        if pdf.stem.lower() == target_pdf.stem.lower():
            score -= 20
        if "build" in [part.lower() for part in pdf.parts]:
            score -= 5
        score += len(pdf.relative_to(latex_root).parts) if _is_relative_to(pdf, latex_root) else 100
        candidates.append((score, pdf, synctex_file))
    if candidates:
        _score, pdf, synctex_file = sorted(candidates, key=lambda item: (item[0], str(item[1]).lower()))[0]
        return SynctexTarget(
            pdf=pdf.resolve(),
            directory=synctex_file.parent.resolve(),
            synctex_file=synctex_file.resolve(),
            source="latex_root_discovery",
        )
    raise RuntimeError(
        f"no SyncTeX target found. Expected {target_pdf.with_suffix('.synctex.gz')} "
        f"or a .synctex(.gz)+PDF pair under {latex_root}. Use --compile-synctex if needed."
    )


def _sidecar_synctex(pdf: Path, directory: Path) -> Path | None:
    names = [pdf.with_suffix(".synctex.gz").name, pdf.with_suffix(".synctex").name]
    for name in names:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def _pdf_for_synctex_file(path: Path) -> Path | None:
    name = path.name
    if name.endswith(".synctex.gz"):
        return path.with_name(name[: -len(".synctex.gz")] + ".pdf")
    if name.endswith(".synctex"):
        return path.with_name(name[: -len(".synctex")] + ".pdf")
    return None


def _pdf_alignment(target_pdf: Path, synctex_pdf: Path) -> dict[str, Any]:
    target = _pdf_fingerprint(target_pdf)
    sync = _pdf_fingerprint(synctex_pdf)
    if target_pdf.resolve() == synctex_pdf.resolve():
        status = "same_file"
    elif target["sha256"] == sync["sha256"]:
        status = "same_hash"
    elif (
        target["page_count"] == sync["page_count"]
        and target["sample_page_sizes"] == sync["sample_page_sizes"]
        and target["sample_text_hashes"] == sync["sample_text_hashes"]
    ):
        status = "same_layout_text"
    elif target["page_count"] == sync["page_count"] and target["sample_page_sizes"] == sync["sample_page_sizes"]:
        status = "same_page_geometry"
    elif target["page_count"] == sync["page_count"]:
        status = "same_page_count_only"
    else:
        status = "mismatch"
    return {
        "status": status,
        "target": target,
        "synctex": sync,
    }


def _pdf_fingerprint(path: Path) -> dict[str, Any]:
    doc = fitz.open(path)
    try:
        sample_pages = _sample_page_indexes(doc.page_count)
        sizes: list[list[float]] = []
        text_hashes: list[str] = []
        for page_num in sample_pages:
            page = doc[page_num]
            rect = page.rect
            sizes.append([round(float(rect.width), 3), round(float(rect.height), 3)])
            text = " ".join(page.get_text("text").split())[:4000]
            text_hashes.append(_stable_hash(text))
        return {
            "path": str(path),
            "sha256": compute_sha256(str(path)),
            "page_count": doc.page_count,
            "sample_pages": sample_pages,
            "sample_page_sizes": sizes,
            "sample_text_hashes": text_hashes,
        }
    finally:
        doc.close()


def _sample_page_indexes(page_count: int) -> list[int]:
    if page_count <= 0:
        return []
    candidates = {0, page_count - 1, page_count // 2}
    if page_count > 5:
        candidates.add(min(page_count - 1, 4))
    return sorted(candidates)


def _located_source(latex_root: Path, record: SourceFormulaRecord) -> SourceLocation:
    tex_path = Path(record.tex_path)
    path = latex_root / tex_path
    warnings: list[str] = []
    input_name = tex_path.as_posix()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return SourceLocation(
            tex_path=record.tex_path,
            input_name=input_name,
            line=1,
            column=1,
            char_start=record.char_start,
            char_end=record.char_end,
            offset_status="source_file_unreadable",
            offset_warnings=(str(exc),),
        )
    start = max(0, min(int(record.char_start), len(text)))
    end = max(start, min(int(record.char_end), len(text)))
    expected = str(record.latex or "").strip()
    actual = text[start:end].strip()
    status = "exact_offset"
    if expected and actual != expected:
        found = text.find(str(record.latex or ""))
        if found >= 0:
            start = found
            end = found + len(str(record.latex or ""))
            status = "substring_relocated"
            warnings.append("source_offset_relocated_by_raw_latex")
        else:
            status = "offset_mismatch"
            warnings.append("source_offset_text_mismatch")
    line, column = _line_column(text, start)
    return SourceLocation(
        tex_path=record.tex_path,
        input_name=input_name,
        line=line,
        column=column,
        char_start=start,
        char_end=end,
        offset_status=status,
        offset_warnings=tuple(warnings),
    )


def _line_column(text: str, offset: int) -> tuple[int, int]:
    offset = max(0, min(int(offset), len(text)))
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset + 1 if last_newline < 0 else offset - last_newline
    return line, max(1, column)


def _run_synctex_queries(
    *,
    case_name: str,
    latex_root: Path,
    target_pdf: Path,
    synctex_target: SynctexTarget,
    pdf_alignment: dict[str, Any],
    records: list[SourceFormulaRecord],
    locations: list[SourceLocation],
    same_line_counts: Counter[tuple[str, str, int]],
    workers: int,
    include_glyphs: bool,
) -> list[dict[str, Any]]:
    if not records:
        return []
    worker_count = max(1, int(workers))
    args = [
        {
            "case_name": case_name,
            "latex_root": latex_root,
            "target_pdf": target_pdf,
            "synctex_target": synctex_target,
            "pdf_alignment": pdf_alignment,
            "record": record,
            "location": location,
            "same_line_count": same_line_counts[_line_scope_key(case_name, location)],
            "include_glyphs": include_glyphs,
        }
        for record, location in zip(records, locations, strict=False)
    ]
    if worker_count == 1:
        return [_anchor_for_record(arg) for arg in args]
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_anchor_for_record, arg) for arg in args]
        for future in as_completed(futures):
            rows.append(future.result())
    return sorted(rows, key=lambda row: (str(row.get("case", "")), str(row.get("source_id", ""))))


def _anchor_for_record(arg: dict[str, Any]) -> dict[str, Any]:
    case_name = str(arg["case_name"])
    latex_root: Path = arg["latex_root"]
    target_pdf: Path = arg["target_pdf"]
    target: SynctexTarget = arg["synctex_target"]
    record: SourceFormulaRecord = arg["record"]
    location: SourceLocation = arg["location"]
    pdf_alignment: dict[str, Any] = arg["pdf_alignment"]
    include_glyphs = bool(arg.get("include_glyphs", True))

    query = _query_synctex(
        latex_root=latex_root,
        target=target,
        location=location,
        page_hint=record.pdf_page_hint,
    )
    best = _best_synctex_result(query["results"], record)
    bbox = _bbox_from_result(best)
    page_num = (int(best.page) - 1) if best is not None and best.page is not None else None
    glyph_pdf = target.pdf if pdf_alignment["status"] not in {"same_file", "same_hash", "same_layout_text"} else target_pdf
    glyph_evidence = (
        _glyph_evidence(glyph_pdf, page_num, bbox)
        if include_glyphs and page_num is not None and bbox is not None
        else _empty_glyph_evidence(str(glyph_pdf))
    )
    blockers = _anchor_blockers(
        record=record,
        location=location,
        query=query,
        best=best,
        bbox=bbox,
        pdf_alignment_status=str(pdf_alignment["status"]),
        same_line_count=int(arg.get("same_line_count", 0) or 0),
        glyph_evidence=glyph_evidence,
    )
    source_anchor_blockers = [
        item for item in blockers
        if item
        not in {
            "target_pdf_alignment_not_verified",
            "same_tex_line_has_multiple_source_formulas",
            "line_window_not_exact_formula_crop",
        }
    ]
    verified = not source_anchor_blockers
    usable_training_row = verified and "same_tex_line_has_multiple_source_formulas" not in blockers
    target_pdf_transferable = verified and pdf_alignment["status"] in {
        "same_file",
        "same_hash",
        "same_layout_text",
        "same_page_geometry",
    }
    return {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "case": case_name,
        "source_id": record.source_id,
        "kind": record.kind,
        "raw_latex": record.latex,
        "canonical_latex": record.canonical_latex,
        "normalized": record.normalized,
        "token_count": record.token_count,
        "tex_path": record.tex_path,
        "line": location.line,
        "column": location.column,
        "char_start": location.char_start,
        "char_end": location.char_end,
        "source_offset_status": location.offset_status,
        "source_offset_warnings": list(location.offset_warnings),
        "env": record.env,
        "delimiter": record.delimiter,
        "context_before": record.context_before,
        "context_after": record.context_after,
        "macro_expansion_version": record.macro_expansion_version,
        "macro_expansion_applied": list(record.macro_expansion_applied),
        "macro_expansion_warnings": list(record.macro_expansion_warnings),
        "pdf_page_hint": record.pdf_page_hint,
        "pdf_page_window_start": record.pdf_page_window_start,
        "pdf_page_window_end": record.pdf_page_window_end,
        "target_pdf": str(target_pdf),
        "synctex_pdf": str(target.pdf),
        "synctex_file": str(target.synctex_file),
        "synctex_dir": str(target.directory),
        "synctex_input": query["input_name"],
        "synctex_status": query["status"],
        "synctex_exit_code": query["exit_code"],
        "synctex_error": query["error"],
        "synctex_results": [asdict(result) for result in query["results"]],
        "anchor_page": best.page if best is not None else None,
        "anchor_page_num": page_num,
        "anchor_bbox": bbox,
        "anchor_bbox_source": "synctex_line_box" if bbox is not None else "",
        "pdf_alignment_status": pdf_alignment["status"],
        "same_tex_line_formula_count": int(arg.get("same_line_count", 0) or 0),
        "glyph_evidence": glyph_evidence,
        "verified_source_anchor": bool(verified),
        "target_pdf_transferable": bool(target_pdf_transferable),
        "usable_line_training_row": bool(usable_training_row),
        "blockers": blockers,
    }


def _query_synctex(
    *,
    latex_root: Path,
    target: SynctexTarget,
    location: SourceLocation,
    page_hint: int | None,
) -> dict[str, Any]:
    synctex = shutil.which("synctex")
    if not synctex:
        return {
            "status": "missing_synctex_executable",
            "exit_code": -1,
            "error": "synctex not found on PATH",
            "input_name": location.input_name,
            "results": [],
        }
    output_arg = _path_for_cwd(target.pdf, latex_root)
    directory_arg = _path_for_cwd(target.directory, latex_root)
    input_names = _input_name_candidates(location, latex_root)
    errors: list[str] = []
    for input_name in input_names:
        specs = _synctex_input_specs(location, page_hint, input_name)
        for input_spec in specs:
            args = [synctex, "view", "-i", input_spec, "-o", output_arg, "-d", directory_arg]
            result = subprocess.run(
                args,
                cwd=str(latex_root),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
            )
            parsed = _parse_synctex_output(result.stdout)
            if result.returncode == 0 and parsed:
                return {
                    "status": "done",
                    "exit_code": result.returncode,
                    "error": "",
                    "input_name": input_name,
                    "input_spec": input_spec,
                    "results": parsed,
                }
            errors.append(_compact_error(result.stdout))
    return {
        "status": "no_result",
        "exit_code": 0,
        "error": " | ".join(item for item in errors if item)[:1000],
        "input_name": input_names[0] if input_names else location.input_name,
        "input_spec": "",
        "results": [],
    }


def _input_name_candidates(location: SourceLocation, latex_root: Path) -> list[str]:
    rel = location.input_name.replace("\\", "/")
    candidates = [rel]
    if not rel.startswith("./"):
        candidates.append("./" + rel)
    absolute = (latex_root / location.tex_path).resolve().as_posix()
    candidates.append(absolute)
    return list(dict.fromkeys(candidates))


def _synctex_input_specs(location: SourceLocation, page_hint: int | None, input_name: str) -> list[str]:
    line = int(location.line)
    column = int(location.column)
    specs = [
        f"{line}:{column}:{input_name}",
        f"{line}:0:{input_name}",
    ]
    if page_hint is not None:
        specs.extend(
            [
                f"{line}:{column}:{int(page_hint)}:{input_name}",
                f"{line}:0:{int(page_hint)}:{input_name}",
            ]
        )
    return list(dict.fromkeys(specs))


def _parse_synctex_output(text: str) -> list[SynctexResult]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    inside = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("SyncTeX result begin"):
            inside = True
            current = {}
            continue
        if line.startswith("SyncTeX result end"):
            if current:
                records.append(current)
            break
        if not inside:
            continue
        if line.startswith("Output:") and current:
            records.append(current)
            current = {}
        if ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = value.strip()
    return [_synctex_result_from_record(record) for record in records if record]


def _synctex_result_from_record(record: dict[str, str]) -> SynctexResult:
    return SynctexResult(
        output=record.get("Output", ""),
        page=_optional_int(record.get("Page")),
        x=_optional_float(record.get("x")),
        y=_optional_float(record.get("y")),
        h=_optional_float(record.get("h")),
        v=_optional_float(record.get("v")),
        width=_optional_float(record.get("W")),
        height=_optional_float(record.get("H")),
        before=record.get("before", ""),
        offset=_optional_int(record.get("offset")),
        middle=record.get("middle", ""),
        after=record.get("after", ""),
    )


def _best_synctex_result(results: list[SynctexResult], record: SourceFormulaRecord) -> SynctexResult | None:
    if not results:
        return None
    start = record.pdf_page_window_start or record.pdf_page_hint
    end = record.pdf_page_window_end
    if start is None:
        return results[0]
    for result in results:
        if result.page is None:
            continue
        if end is None and result.page >= start:
            return result
        if end is not None and start <= result.page <= end:
            return result
    return results[0]


def _bbox_from_result(result: SynctexResult | None) -> list[float] | None:
    if result is None:
        return None
    values = [result.x, result.y, result.h, result.v, result.width, result.height]
    if any(value is None for value in values):
        return None
    x = float(result.x or 0.0)
    y = float(result.y or 0.0)
    h = float(result.h or x)
    v = float(result.v or y)
    width = abs(float(result.width or 0.0))
    height = abs(float(result.height or 0.0))
    pad = max(2.0, min(12.0, height * 0.35))
    xs = [x, h, h + width, x + width]
    ys = [y, v, v - height, v + height, y - height, y + height]
    return [
        round(max(0.0, min(xs) - pad), 3),
        round(max(0.0, min(ys) - pad), 3),
        round(max(xs) + pad, 3),
        round(max(ys) + pad, 3),
    ]


def _glyph_evidence(pdf: Path, page_num: int | None, bbox: list[float] | None) -> dict[str, Any]:
    if page_num is None or bbox is None:
        return _empty_glyph_evidence(str(pdf))
    doc = fitz.open(pdf)
    try:
        if page_num < 0 or page_num >= doc.page_count:
            evidence = _empty_glyph_evidence(str(pdf))
            evidence["warnings"] = ["anchor_page_out_of_range"]
            return evidence
        page = doc[page_num]
        rect = fitz.Rect(bbox)
        raw = page.get_text("rawdict")
        glyphs: list[dict[str, Any]] = []
        fonts: Counter[str] = Counter()
        sizes: list[float] = []
        for block in raw.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = str(span.get("font", ""))
                    size = _optional_float(span.get("size")) or 0.0
                    for char in span.get("chars", []):
                        cb = char.get("bbox")
                        if not _valid_bbox(cb):
                            continue
                        char_rect = fitz.Rect(cb)
                        if not char_rect.intersects(rect):
                            continue
                        text = str(char.get("c", ""))
                        fonts[font] += 1
                        sizes.append(size)
                        if len(glyphs) < 400:
                            glyphs.append(
                                {
                                    "text": text,
                                    "font": font,
                                    "size": round(float(size), 6),
                                    "bbox": [round(float(value), 3) for value in cb],
                                }
                            )
        text = "".join(item["text"] for item in glyphs)
        return {
            "pdf": str(pdf),
            "page_num": page_num,
            "bbox": bbox,
            "glyph_count": sum(fonts.values()),
            "sampled_glyphs": glyphs,
            "text_sample": text[:500],
            "fonts": dict(sorted(fonts.items())),
            "font_count": len(fonts),
            "font_size_min": round(min(sizes), 6) if sizes else None,
            "font_size_max": round(max(sizes), 6) if sizes else None,
            "warnings": [] if glyphs else ["empty_glyph_window"],
        }
    finally:
        doc.close()


def _empty_glyph_evidence(pdf: str) -> dict[str, Any]:
    return {
        "pdf": pdf,
        "page_num": None,
        "bbox": None,
        "glyph_count": 0,
        "sampled_glyphs": [],
        "text_sample": "",
        "fonts": {},
        "font_count": 0,
        "font_size_min": None,
        "font_size_max": None,
        "warnings": ["glyph_evidence_not_collected"],
    }


def _anchor_blockers(
    *,
    record: SourceFormulaRecord,
    location: SourceLocation,
    query: dict[str, Any],
    best: SynctexResult | None,
    bbox: list[float] | None,
    pdf_alignment_status: str,
    same_line_count: int,
    glyph_evidence: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if location.offset_status not in {"exact_offset", "substring_relocated"}:
        blockers.append("source_offset_not_verified")
    if record.macro_expansion_warnings:
        blockers.append("macro_expansion_warnings_present")
    if query.get("status") != "done" or best is None:
        blockers.append("missing_synctex_anchor")
    if bbox is None:
        blockers.append("missing_anchor_bbox")
    if pdf_alignment_status not in {"same_file", "same_hash", "same_layout_text", "same_page_geometry"}:
        blockers.append("target_pdf_alignment_not_verified")
    if best is not None and best.page is not None and not _page_in_record_window(best.page, record):
        blockers.append("anchor_outside_source_page_window")
    if int(glyph_evidence.get("glyph_count", 0) or 0) <= 0:
        blockers.append("empty_glyph_window")
    if same_line_count > 1:
        blockers.append("same_tex_line_has_multiple_source_formulas")
    blockers.append("line_window_not_exact_formula_crop")
    return _dedupe(blockers)


def _page_in_record_window(page: int, record: SourceFormulaRecord) -> bool:
    start = record.pdf_page_window_start or record.pdf_page_hint
    end = record.pdf_page_window_end
    if start is None:
        return True
    if end is None:
        return int(page) >= int(start)
    return int(start) <= int(page) <= int(end)


def _training_row(anchor: dict[str, Any]) -> dict[str, Any]:
    glyph = anchor.get("glyph_evidence", {})
    if not isinstance(glyph, dict):
        glyph = {}
    return {
        "schema_version": TRAINING_ROW_SCHEMA_VERSION,
        "case": anchor.get("case", ""),
        "source_id": anchor.get("source_id", ""),
        "kind": anchor.get("kind", ""),
        "label_latex": anchor.get("canonical_latex", ""),
        "raw_source_latex": anchor.get("raw_latex", ""),
        "label_source": "latex_source_macro_expanded",
        "pdf_window_source": "synctex_line_box",
        "pdf": glyph.get("pdf", anchor.get("synctex_pdf", "")),
        "page_num": anchor.get("anchor_page_num"),
        "bbox": anchor.get("anchor_bbox"),
        "tex_path": anchor.get("tex_path", ""),
        "line": anchor.get("line"),
        "column": anchor.get("column"),
        "glyph_count": glyph.get("glyph_count", 0),
        "glyph_text_sample": glyph.get("text_sample", ""),
        "fonts": glyph.get("fonts", {}),
        "sampled_glyphs": glyph.get("sampled_glyphs", []),
        "verified_source_anchor": anchor.get("verified_source_anchor", False),
        "known_limitations": ["line_window_not_exact_formula_crop"],
        "anchor_ref": {
            "synctex_file": anchor.get("synctex_file", ""),
            "synctex_input": anchor.get("synctex_input", ""),
            "pdf_alignment_status": anchor.get("pdf_alignment_status", ""),
        },
    }


def _line_scope_key(case_name: str, location: SourceLocation) -> tuple[str, str, int]:
    return (case_name, location.tex_path.replace("\\", "/"), int(location.line))


def _path_for_cwd(path: Path, cwd: Path) -> str:
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    import hashlib

    return hashlib.sha256(encoded).hexdigest()[:16]


def _valid_bbox(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 4


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact_error(value: str) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return " / ".join(lines[-6:])[:400]


def _blocker_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for blocker in row.get("blockers", []) or []:
            counts[str(blocker)] += 1
    return dict(sorted(counts.items()))


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


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
    parser.add_argument("--case", default="napkin", help="attention, napkin, all, or a custom case name with --pdf/--latex-root")
    parser.add_argument("--pdf", type=Path, default=None)
    parser.add_argument("--latex-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0, help="0 means all source/PDF pages")
    parser.add_argument("--match-scope", choices=["all", "display", "inline"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="0 means no source formula limit")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--synctex-pdf", type=Path, default=None)
    parser.add_argument("--synctex-dir", type=Path, default=None)
    parser.add_argument("--compile-synctex", action="store_true")
    parser.add_argument("--main-tex", type=Path, default=None)
    parser.add_argument("--no-glyphs", action="store_true", help="Only query SyncTeX; skip PDF glyph-window extraction")
    args = parser.parse_args()
    build_synctex_dataset(
        case_name=args.case,
        output_dir=args.output_dir,
        custom_pdf=args.pdf,
        custom_latex_root=args.latex_root,
        start_page=args.start_page,
        max_pages=args.max_pages,
        match_scope=args.match_scope,
        limit=args.limit,
        workers=args.workers,
        synctex_pdf=args.synctex_pdf,
        synctex_dir=args.synctex_dir,
        compile_synctex=args.compile_synctex,
        main_tex=args.main_tex,
        include_glyphs=not args.no_glyphs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
