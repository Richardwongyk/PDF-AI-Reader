"""Build real born-digital formula datasets from bundled PDF/LaTeX fixtures.

This tool is for validation and model-data preparation.  It uses the provided
LaTeX sources only as ground truth for audits; production parsing paths do not
depend on source files.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.born_digital_formula_extractor import BornDigitalFormulaStructureExtractor
from src.core.born_digital_math import MuPDFBornDigitalExtractor
from src.core.latex_macro_expander import LatexMacroExpander, load_latex_macro_expander
from src.core.latex_math_source_parser import extract_latex_math_spans
from src.core.pdf_glyph_graph import RawGlyphGraphExtractor
from src.core.symbol_identity_repair import SymbolIdentityRepairer
from src.core.tinybdmath_features import TinyBDFeatureExtractor, TinyBDFeatureGraph
from src.infra.file_hash import compute_sha256
from tools.formula_latex_audit import (
    _cases,
    _extract_source_formulas_detailed,
    _formula_similarity,
    _normalize_formula_for_match,
    _ordered_source_entries,
    _relative_source_path,
    _select_source_entries_for_pdf_pages,
)


@dataclass
class SourceFormulaRecord:
    source_id: str
    kind: str
    latex: str
    canonical_latex: str
    normalized: str
    token_count: int
    tex_path: str
    tex_order: int
    char_start: int
    char_end: int
    env: str
    context_before: str
    context_after: str
    delimiter: str = ""
    parser_version: str = "latex_math_source_parser_v1"
    macro_expansion_version: str = ""
    macro_expansion_applied: tuple[str, ...] = ()
    macro_expansion_warnings: tuple[str, ...] = ()
    pdf_page_hint: int | None = None
    pdf_page_window_start: int | None = None
    pdf_page_window_end: int | None = None
    page_alignment_source: str = ""


@dataclass
class FormulaDatasetCandidate:
    candidate_id: str
    page_num: int
    bbox: tuple[float, float, float, float]
    pdf_text: str
    r0_latex: str
    r0_score: float
    r0_input_hash: str
    raw_graph_hash: str
    enriched_graph_hash: str
    enriched_summary: dict[str, Any]
    feature_graph_hash: str
    glyph_count: int
    edge_count: int
    edge_hint_counts: dict[str, int]
    feature_graph_jsonl_id: str
    best_source_similarity: float
    best_source_id: str
    best_source_latex: str
    best_source_raw_latex: str
    match_rank: int
    page_match: str
    source_pdf_page_hint: int | None
    source_pdf_page_window_start: int | None
    source_pdf_page_window_end: int | None
    source_macro_expansion_warnings: list[str]
    warnings: list[str]


@dataclass
class FormulaDatasetReport:
    case: str
    pdf: str
    latex_root: str
    doc_hash: str
    elapsed_sec: float
    pages_scanned: int
    source_formulas: int
    source_display_formulas: int
    source_inline_formulas: int
    candidates: int
    average_best_similarity: float
    weak_match_rate: float
    near_match_rate: float
    raw_unknown_glyphs: int
    repaired_glyphs: int
    feature_edges: int
    edge_hint_totals: dict[str, int]
    source_index: list[SourceFormulaRecord]
    candidate_reports: list[FormulaDatasetCandidate]
    feature_graph_rows: list[dict[str, Any]]


def _select_cases(case_name: str) -> list[Any]:
    cases = _cases()
    if case_name == "all":
        return cases
    return [case for case in cases if case.name == case_name]


def custom_case(name: str, pdf: Path, latex_root: Path) -> Any:
    class _Case:
        pass

    case = _Case()
    case.name = name
    case.pdf = pdf
    case.latex_root = latex_root
    return case


def _source_index(
    case: Any,
    max_pages: int,
    match_scope: str,
    *,
    start_page: int = 0,
) -> tuple[list[SourceFormulaRecord], int, int]:
    source_page_limit = max(0, int(start_page)) + max(0, int(max_pages)) if max_pages > 0 else 0
    extraction = _extract_source_formulas_detailed(
        case.latex_root,
        pdf=case.pdf,
        max_pages=source_page_limit,
    )
    macro_expander = load_latex_macro_expander(case.latex_root)
    entries, _coverage = _select_source_entries_for_pdf_pages(
        _ordered_source_entries(case.latex_root),
        latex_root=case.latex_root,
        pdf=case.pdf,
        max_pages=source_page_limit,
    )
    records: list[SourceFormulaRecord] = []
    page_windows = _entry_page_windows(entries, max_pages=source_page_limit)
    for entry in entries:
        window_start, window_end = page_windows.get(int(entry.order), (None, None))
        for item in _source_formula_records_for_entry(
            case.latex_root,
            entry,
            macro_expander=macro_expander,
            page_window_start=window_start,
            page_window_end=window_end,
        ):
            if match_scope != "all" and item.kind != match_scope:
                continue
            records.append(item)
    for index, record in enumerate(records):
        records[index] = SourceFormulaRecord(
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
            delimiter=record.delimiter,
            parser_version=record.parser_version,
            macro_expansion_version=record.macro_expansion_version,
            macro_expansion_applied=record.macro_expansion_applied,
            macro_expansion_warnings=record.macro_expansion_warnings,
            pdf_page_hint=record.pdf_page_hint,
            pdf_page_window_start=record.pdf_page_window_start,
            pdf_page_window_end=record.pdf_page_window_end,
            page_alignment_source=record.page_alignment_source,
            context_before=record.context_before,
            context_after=record.context_after,
        )
    return records, len(extraction.display), len(extraction.inline)


def _entry_page_windows(entries: list[Any], *, max_pages: int) -> dict[int, tuple[int | None, int | None]]:
    mapped: list[tuple[int, int]] = []
    for entry in entries:
        page = getattr(entry, "effective_page", None)
        if page is None:
            continue
        try:
            mapped.append((int(entry.order), int(page)))
        except (TypeError, ValueError):
            continue
    next_by_order: dict[int, int | None] = {}
    for index, (order, page) in enumerate(mapped):
        next_page: int | None = None
        for _next_order, candidate_page in mapped[index + 1 :]:
            if candidate_page > page:
                next_page = candidate_page
                break
        next_by_order[order] = next_page

    current_page: int | None = None
    current_next: int | None = None
    result: dict[int, tuple[int | None, int | None]] = {}
    for entry in entries:
        page = getattr(entry, "effective_page", None)
        if page is not None:
            try:
                current_page = int(page)
                current_next = next_by_order.get(int(entry.order))
            except (TypeError, ValueError):
                current_page = None
                current_next = None
        end_page = current_next - 1 if current_next is not None and current_page is not None else None
        if max_pages > 0 and current_page is not None:
            end_page = min(end_page if end_page is not None else max_pages, max_pages)
        if current_page is not None and end_page is not None and end_page < current_page:
            end_page = current_page
        result[int(entry.order)] = (current_page, end_page)
    return result


def _source_formula_records_for_entry(
    latex_root: Path,
    entry: Any,
    *,
    macro_expander: LatexMacroExpander,
    page_window_start: int | None = None,
    page_window_end: int | None = None,
) -> list[SourceFormulaRecord]:
    records: list[SourceFormulaRecord] = []
    text = str(entry.text or "")
    for span in extract_latex_math_spans(text):
        records.append(
            _source_record(
                latex_root,
                entry,
                span.body,
                span.kind,
                span.env,
                span.body_start,
                span.body_end,
                macro_expander=macro_expander,
                delimiter=span.delimiter,
                page_window_start=page_window_start,
                page_window_end=page_window_end,
            )
        )
    return sorted(records, key=lambda item: (item.tex_order, item.char_start, item.char_end))


def _source_record(
    latex_root: Path,
    entry: Any,
    latex: str,
    kind: str,
    env: str,
    start: int,
    end: int,
    macro_expander: LatexMacroExpander,
    delimiter: str = "",
    page_window_start: int | None = None,
    page_window_end: int | None = None,
) -> SourceFormulaRecord:
    expansion = macro_expander.expand(latex)
    canonical_latex = expansion.latex or latex.strip()
    normalized = _normalize_formula_for_match(canonical_latex)
    text = str(entry.text or "")
    return SourceFormulaRecord(
        source_id="",
        kind=kind,
        latex=latex.strip(),
        canonical_latex=canonical_latex,
        normalized=normalized,
        token_count=len(_match_tokens(normalized)),
        tex_path=_relative_source_path(entry.path, latex_root),
        tex_order=int(entry.order),
        char_start=int(start),
        char_end=int(end),
        env=env,
        delimiter=delimiter,
        macro_expansion_version=expansion.version,
        macro_expansion_applied=expansion.applied_macros,
        macro_expansion_warnings=expansion.warnings,
        pdf_page_hint=page_window_start,
        pdf_page_window_start=page_window_start,
        pdf_page_window_end=page_window_end,
        page_alignment_source="pdf_toc_effective_page" if page_window_start is not None else "",
        context_before=" ".join(text[max(0, start - 180) : start].split())[-180:],
        context_after=" ".join(text[end : end + 180].split())[:180],
    )


def build_case_dataset(
    case: Any,
    *,
    start_page: int = 0,
    max_pages: int = 0,
    match_scope: str = "all",
) -> FormulaDatasetReport:
    started = time.perf_counter()
    source_index, display_count, inline_count = _source_index(
        case,
        max_pages,
        match_scope,
        start_page=start_page,
    )
    source_match_index = _SourceMatchIndex(source_index)
    doc_hash = compute_sha256(str(case.pdf))[:16]
    page_extractor = MuPDFBornDigitalExtractor()
    raw_extractor = RawGlyphGraphExtractor()
    structure_extractor = BornDigitalFormulaStructureExtractor(min_confidence=0.0)
    repairer = SymbolIdentityRepairer(auto_discover_glyph_maps=True)
    feature_extractor = TinyBDFeatureExtractor()

    candidate_reports: list[FormulaDatasetCandidate] = []
    feature_graph_rows: list[dict[str, Any]] = []
    raw_unknown_glyphs = 0
    repaired_glyphs = 0
    feature_edges = 0
    edge_hint_totals: Counter[str] = Counter()
    doc = fitz.open(case.pdf)
    try:
        first_page = max(0, min(start_page, doc.page_count))
        last_page = doc.page_count if max_pages <= 0 else min(doc.page_count, first_page + max_pages)
        for page_num in range(first_page, last_page):
            page_facts = page_extractor.extract_page(doc[page_num], page_num)
            raw_graph = raw_extractor.from_page_facts(page_facts)
            enriched_graph = repairer.repair_graph(raw_graph)
            raw_unknown_glyphs += raw_graph.health.unknown_glyph_count
            repaired_glyphs += enriched_graph.summary.repaired_count
            candidates = structure_extractor.extract_candidates_from_page_graphs(
                page_facts,
                raw_graph,
                enriched_graph,
            )
            for candidate in candidates:
                feature_graph = feature_extractor.extract_region(enriched_graph, candidate.bbox)
                feature_edges += len(feature_graph.edges)
                hint_counts = _edge_hint_counts(feature_graph)
                edge_hint_totals.update(hint_counts)
                match = source_match_index.best_match(candidate.latex, page_num=page_num)
                feature_graph_row = {
                    "case": case.name,
                    "candidate_id": candidate.candidate_id,
                    "page_num": page_num,
                    "bbox": candidate.bbox,
                    "feature_graph": feature_graph.to_json(),
                }
                feature_graph_rows.append(feature_graph_row)
                candidate_reports.append(
                    FormulaDatasetCandidate(
                        candidate_id=candidate.candidate_id,
                        page_num=page_num,
                        bbox=candidate.bbox,
                        pdf_text=candidate.text,
                        r0_latex=candidate.latex,
                        r0_score=round(float(candidate.confidence), 6),
                        r0_input_hash=candidate.input_hash,
                        raw_graph_hash=raw_graph.input_hash,
                        enriched_graph_hash=enriched_graph.input_hash,
                        enriched_summary=asdict(enriched_graph.summary),
                        feature_graph_hash=feature_graph.input_hash,
                        glyph_count=len(feature_graph.glyphs),
                        edge_count=len(feature_graph.edges),
                        edge_hint_counts=hint_counts,
                        feature_graph_jsonl_id=f"{case.name}:{candidate.candidate_id}",
                        best_source_similarity=match["similarity"],
                        best_source_id=str(match["source_id"]),
                        best_source_latex=str(match["latex"]),
                        best_source_raw_latex=str(match.get("raw_latex", "")),
                        match_rank=int(match["rank"]),
                        page_match=str(match.get("page_match", "")),
                        source_pdf_page_hint=_optional_int(match.get("pdf_page_hint")),
                        source_pdf_page_window_start=_optional_int(match.get("pdf_page_window_start")),
                        source_pdf_page_window_end=_optional_int(match.get("pdf_page_window_end")),
                        source_macro_expansion_warnings=list(match.get("macro_expansion_warnings", [])),
                        warnings=list(candidate.warnings),
                    )
                )
        pages_scanned = max(0, last_page - first_page)
    finally:
        doc.close()
    similarities = [item.best_source_similarity for item in candidate_reports]
    return FormulaDatasetReport(
        case=case.name,
        pdf=str(case.pdf),
        latex_root=str(case.latex_root),
        doc_hash=doc_hash,
        elapsed_sec=round(time.perf_counter() - started, 3),
        pages_scanned=pages_scanned,
        source_formulas=len(source_index),
        source_display_formulas=display_count,
        source_inline_formulas=inline_count,
        candidates=len(candidate_reports),
        average_best_similarity=round(sum(similarities) / len(similarities), 3) if similarities else 1.0,
        weak_match_rate=round(sum(1 for value in similarities if value >= 0.55) / len(similarities), 3) if similarities else 1.0,
        near_match_rate=round(sum(1 for value in similarities if value >= 0.80) / len(similarities), 3) if similarities else 1.0,
        raw_unknown_glyphs=raw_unknown_glyphs,
        repaired_glyphs=repaired_glyphs,
        feature_edges=feature_edges,
        edge_hint_totals=dict(sorted(edge_hint_totals.items())),
        source_index=source_index,
        candidate_reports=candidate_reports,
        feature_graph_rows=feature_graph_rows,
    )


class _SourceMatchIndex:
    def __init__(self, source_index: list[SourceFormulaRecord]) -> None:
        self.records = source_index
        self.token_index: dict[str, list[int]] = {}
        self.page_index: dict[int, list[int]] = {}
        self.open_page_indexes: list[int] = []
        for index, record in enumerate(source_index):
            for token in _match_tokens(record.normalized):
                self.token_index.setdefault(token, []).append(index)
            pages = list(self._record_pages(record))
            if not pages and record.pdf_page_window_start is not None:
                self.open_page_indexes.append(index)
            for page in pages:
                self.page_index.setdefault(page, []).append(index)

    def best_match(self, latex: str, page_num: int | None = None, max_candidates: int = 80) -> dict[str, object]:
        normalized = _normalize_formula_for_match(latex)
        if not normalized or not self.records:
            return {"source_id": "", "latex": "", "similarity": 0.0, "rank": -1}
        candidate_ids = self._candidate_ids(
            normalized,
            page_num=page_num,
            max_candidates=max_candidates,
        )
        best_score = 0.0
        best_index = -1
        rank = -1
        for index, source_index in enumerate(candidate_ids):
            record = self.records[source_index]
            score = _formula_similarity(normalized, record.normalized)
            if score > best_score:
                best_score = score
                best_index = source_index
                rank = index
        if best_index < 0:
            return {"source_id": "", "latex": "", "similarity": 0.0, "rank": -1}
        record = self.records[best_index]
        return {
            "source_id": record.source_id,
            "raw_latex": " ".join(record.latex.split())[:240],
            "canonical_latex": " ".join((record.canonical_latex or record.latex).split())[:240],
            "latex": " ".join((record.canonical_latex or record.latex).split())[:240],
            "similarity": round(best_score, 3),
            "rank": rank,
            "page_match": self._page_match_label(record, page_num),
            "pdf_page_hint": record.pdf_page_hint,
            "pdf_page_window_start": record.pdf_page_window_start,
            "pdf_page_window_end": record.pdf_page_window_end,
            "macro_expansion_warnings": list(record.macro_expansion_warnings),
        }

    def _candidate_ids(self, normalized: str, page_num: int | None, max_candidates: int) -> list[int]:
        counts: Counter[int] = Counter()
        for token in _match_tokens(normalized):
            counts.update(self.token_index.get(token, []))
        scoped_indexes = self._scoped_page_indexes(page_num)
        scoped_set = set(scoped_indexes)
        if not counts:
            base = scoped_indexes[:max_candidates] if scoped_indexes else list(range(min(len(self.records), max_candidates)))
        else:
            base = sorted(
                counts,
                key=lambda index: (
                    0 if index in scoped_set else 1,
                    -counts[index],
                    abs(len(self.records[index].normalized) - len(normalized)),
                    index,
                ),
            )[: max(max_candidates * 6, max_candidates)]
        if page_num is None:
            return base[:max_candidates]
        pdf_page = int(page_num) + 1
        same_window = [index for index in base if self._record_covers_page(self.records[index], pdf_page)]
        if len(same_window) >= max_candidates:
            return same_window[:max_candidates]
        nearby = [
            index for index in base
            if index not in set(same_window)
            and self._record_near_page(self.records[index], pdf_page, radius=2)
        ]
        selected = same_window + nearby
        if len(selected) >= max_candidates:
            return selected[:max_candidates]
        selected_set = set(selected)
        selected.extend(index for index in base if index not in selected_set)
        return selected[:max_candidates]

    def _scoped_page_indexes(self, page_num: int | None) -> list[int]:
        if page_num is None:
            return []
        pdf_page = int(page_num) + 1
        exact = self._unique_indexes(
            list(self.page_index.get(pdf_page, []))
            + [
                index for index in self.open_page_indexes
                if self._record_covers_page(self.records[index], pdf_page)
            ]
        )
        if exact:
            return exact
        nearby_pages: list[int] = []
        for page in range(max(1, pdf_page - 2), pdf_page + 3):
            nearby_pages.extend(self.page_index.get(page, []))
        nearby_pages.extend(
            index for index in self.open_page_indexes
            if self._record_near_page(self.records[index], pdf_page, radius=2)
        )
        return self._unique_indexes(nearby_pages)

    def _unique_indexes(self, indexes: Iterable[int]) -> list[int]:
        seen: set[int] = set()
        result: list[int] = []
        for index in indexes:
            if index in seen:
                continue
            seen.add(index)
            result.append(index)
        return result

    def _record_pages(self, record: SourceFormulaRecord) -> list[int]:
        start = record.pdf_page_window_start or record.pdf_page_hint
        if start is None:
            return []
        if record.pdf_page_window_end is None:
            return []
        end = record.pdf_page_window_end or start
        return list(range(max(1, int(start)), max(1, int(end)) + 1))

    def _record_covers_page(self, record: SourceFormulaRecord, pdf_page: int) -> bool:
        start = record.pdf_page_window_start or record.pdf_page_hint
        if start is None:
            return False
        if record.pdf_page_window_end is None:
            return pdf_page >= int(start)
        return int(start) <= pdf_page <= int(record.pdf_page_window_end)

    def _record_near_page(self, record: SourceFormulaRecord, pdf_page: int, *, radius: int) -> bool:
        start = record.pdf_page_window_start or record.pdf_page_hint
        if start is None:
            return False
        if record.pdf_page_window_end is None:
            return pdf_page >= int(start) - radius
        return int(start) - radius <= pdf_page <= int(record.pdf_page_window_end) + radius

    def _page_match_label(self, record: SourceFormulaRecord, page_num: int | None) -> str:
        if page_num is None:
            return "unscoped"
        pdf_page = int(page_num) + 1
        if self._record_covers_page(record, pdf_page):
            return "same_page_window"
        if self._record_near_page(record, pdf_page, radius=2):
            return "near_page_window"
        if record.pdf_page_hint is not None:
            return "outside_page_window"
        return "no_source_page_hint"


def _match_tokens(normalized_formula: str) -> set[str]:
    tokens = set()
    compact = normalized_formula.replace("_", "").replace("{", "").replace("}", "")
    tokens.update(part for part in compact.replace("\\", "").split() if part)
    import re

    tokens.update(re.findall(r"[a-z]{2,}|[0-9]+", normalized_formula))
    for length in (4, 6, 8):
        if len(compact) >= length:
            tokens.add(compact[:length])
    return tokens


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _edge_hint_counts(feature_graph: TinyBDFeatureGraph) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in feature_graph.edges:
        hint = str(edge.hint)
        counts[hint] = counts.get(hint, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0, help="0 means all pages")
    parser.add_argument("--match-scope", choices=["all", "display", "inline"], default="all")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--jsonl-dir", type=Path, default=None)
    parser.add_argument("--include-records", action="store_true", help="Embed full records in the summary JSON")
    args = parser.parse_args()

    report_objects = [
        build_case_dataset(
            case,
            start_page=args.start_page,
            max_pages=args.max_pages,
            match_scope=args.match_scope,
        )
        for case in _select_cases(args.case)
    ]
    if args.jsonl_dir is not None:
        _write_jsonl_dataset(args.jsonl_dir, report_objects)
    reports = [_report_payload(report, include_records=args.include_records) for report in report_objects]
    payload: dict[str, Any] = {
        "schema_version": "born_digital_formula_dataset_v1",
        "case": args.case,
        "start_page": args.start_page,
        "max_pages": args.max_pages,
        "match_scope": args.match_scope,
        "reports": reports,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def _write_jsonl_dataset(output_dir: Path, reports: list[FormulaDatasetReport]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for report in reports:
        for source in report.source_index:
            source_rows.append({"case": report.case, **asdict(source)})
        for candidate in report.candidate_reports:
            candidate_rows.append({"case": report.case, **asdict(candidate)})
        feature_rows.extend(report.feature_graph_rows)
    _write_jsonl(output_dir / "source_formulas.jsonl", source_rows)
    _write_jsonl(output_dir / "pdf_candidates.jsonl", candidate_rows)
    _write_jsonl(output_dir / "feature_graphs.jsonl", feature_rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _report_payload(report: FormulaDatasetReport, *, include_records: bool) -> dict[str, Any]:
    payload = asdict(report)
    if not include_records:
        payload["source_index_sample"] = payload["source_index"][:10]
        payload["candidate_reports_sample"] = payload["candidate_reports"][:20]
        payload["feature_graph_rows_sample"] = payload["feature_graph_rows"][:3]
        payload.pop("source_index", None)
        payload.pop("candidate_reports", None)
        payload.pop("feature_graph_rows", None)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
