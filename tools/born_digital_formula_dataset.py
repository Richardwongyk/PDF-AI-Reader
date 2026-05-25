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
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.born_digital_formula_extractor import BornDigitalFormulaStructureExtractor
from src.core.born_digital_math import MuPDFBornDigitalExtractor
from src.core.pdf_glyph_graph import RawGlyphGraphExtractor
from src.core.symbol_identity_repair import SymbolIdentityRepairer
from src.core.tinybdmath_features import TinyBDFeatureExtractor, TinyBDFeatureGraph
from src.infra.file_hash import compute_sha256
from tools.formula_latex_audit import (
    DISPLAY_ENVS,
    SOURCE_FORMULA_PATTERNS,
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
    normalized: str
    token_count: int
    tex_path: str
    tex_order: int
    char_start: int
    char_end: int
    env: str
    context_before: str
    context_after: str


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
    match_rank: int
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


def _source_index(case: Any, max_pages: int, match_scope: str) -> tuple[list[SourceFormulaRecord], int, int]:
    extraction = _extract_source_formulas_detailed(
        case.latex_root,
        pdf=case.pdf,
        max_pages=max_pages,
    )
    entries, _coverage = _select_source_entries_for_pdf_pages(
        _ordered_source_entries(case.latex_root),
        latex_root=case.latex_root,
        pdf=case.pdf,
        max_pages=max_pages,
    )
    records: list[SourceFormulaRecord] = []
    for entry in entries:
        for item in _source_formula_records_for_entry(case.latex_root, entry):
            if match_scope != "all" and item.kind != match_scope:
                continue
            records.append(item)
    for index, record in enumerate(records):
        records[index] = SourceFormulaRecord(
            source_id=f"src_{index:06d}",
            kind=record.kind,
            latex=record.latex,
            normalized=record.normalized,
            token_count=record.token_count,
            tex_path=record.tex_path,
            tex_order=record.tex_order,
            char_start=record.char_start,
            char_end=record.char_end,
            env=record.env,
            context_before=record.context_before,
            context_after=record.context_after,
        )
    return records, len(extraction.display), len(extraction.inline)


def _source_formula_records_for_entry(latex_root: Path, entry: Any) -> list[SourceFormulaRecord]:
    records: list[SourceFormulaRecord] = []
    text = str(entry.text or "")
    for env in DISPLAY_ENVS:
        escaped_env = env.replace("*", r"\*")
        import re

        pattern = re.compile(
            rf"\\begin\{{{escaped_env}\}}(.+?)\\end\{{{escaped_env}\}}",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            records.append(_source_record(latex_root, entry, match.group(1), "display", env, match.start(1), match.end(1)))
    for index, pattern in enumerate(SOURCE_FORMULA_PATTERNS):
        kind = "display" if index in (0, 1) else "inline"
        env = ("bracket_math", "dollar_display", "paren_inline", "dollar_inline")[index]
        for match in pattern.finditer(text):
            records.append(_source_record(latex_root, entry, match.group(1), kind, env, match.start(1), match.end(1)))
    return sorted(records, key=lambda item: (item.tex_order, item.char_start, item.char_end))


def _source_record(
    latex_root: Path,
    entry: Any,
    latex: str,
    kind: str,
    env: str,
    start: int,
    end: int,
) -> SourceFormulaRecord:
    normalized = _normalize_formula_for_match(latex)
    text = str(entry.text or "")
    return SourceFormulaRecord(
        source_id="",
        kind=kind,
        latex=latex.strip(),
        normalized=normalized,
        token_count=len(_match_tokens(normalized)),
        tex_path=_relative_source_path(entry.path, latex_root),
        tex_order=int(entry.order),
        char_start=int(start),
        char_end=int(end),
        env=env,
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
    source_index, display_count, inline_count = _source_index(case, max_pages, match_scope)
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
            candidates = structure_extractor.extract_candidates_from_page_facts(page_facts)
            for candidate in candidates:
                feature_graph = feature_extractor.extract_region(enriched_graph, candidate.bbox)
                feature_edges += len(feature_graph.edges)
                hint_counts = _edge_hint_counts(feature_graph)
                edge_hint_totals.update(hint_counts)
                match = source_match_index.best_match(candidate.latex)
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
                        match_rank=int(match["rank"]),
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
        for index, record in enumerate(source_index):
            for token in _match_tokens(record.normalized):
                self.token_index.setdefault(token, []).append(index)

    def best_match(self, latex: str, max_candidates: int = 80) -> dict[str, object]:
        normalized = _normalize_formula_for_match(latex)
        if not normalized or not self.records:
            return {"source_id": "", "latex": "", "similarity": 0.0, "rank": -1}
        candidate_ids = self._candidate_ids(normalized, max_candidates=max_candidates)
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
            "latex": " ".join(record.latex.split())[:240],
            "similarity": round(best_score, 3),
            "rank": rank,
        }

    def _candidate_ids(self, normalized: str, max_candidates: int) -> list[int]:
        counts: Counter[int] = Counter()
        for token in _match_tokens(normalized):
            counts.update(self.token_index.get(token, []))
        if not counts:
            return list(range(min(len(self.records), max_candidates)))
        return sorted(
            counts,
            key=lambda index: (
                -counts[index],
                abs(len(self.records[index].normalized) - len(normalized)),
                index,
            ),
        )[:max_candidates]


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
