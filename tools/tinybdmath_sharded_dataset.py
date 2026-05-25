"""Fast resumable TinyBDMath dataset builder.

This tool is the production data-engineering path for Attention/Napkin and
future large PDFs.  It writes page-level shards immediately, records a manifest,
and can resume after interruption.  The final JSONL files keep the same layout
as ``tools/born_digital_formula_dataset.py`` so existing training scripts work.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
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
from src.core.tinybdmath_features import TinyBDFeatureExtractor
from src.infra.file_hash import compute_sha256
from tools.born_digital_formula_dataset import (
    FormulaDatasetCandidate,
    SourceFormulaRecord,
    _SourceMatchIndex,
    _edge_hint_counts,
    _select_cases,
    _source_index,
)
from tools.tinybdmath_dataset_audit import audit_dataset
from tools.tinybdmath_gold_audit import audit_gold
from tools.tinybdmath_training_data import build_training_rows, _write_jsonl


MANIFEST_VERSION = "tinybdmath_sharded_dataset_v1"
SHARD_PREPROCESS_VERSION = "tinybdmath_pdf_source_page_anchor_v2"


@dataclass(frozen=True)
class PageShardResult:
    case: str
    page_num: int
    status: str
    elapsed_sec: float
    raw_unknown_glyphs: int
    repaired_glyphs: int
    candidates: int
    feature_edges: int
    error: str = ""


def build_sharded_dataset(
    *,
    case_name: str,
    output_dir: Path,
    start_page: int = 0,
    max_pages: int = 0,
    workers: int = 1,
    resume: bool = True,
    match_scope: str = "all",
) -> dict[str, Any]:
    started = time.perf_counter()
    dataset_dir = output_dir / "dataset"
    shard_dir = output_dir / "page_shards"
    manifest_dir = output_dir / "manifests"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    shard_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    all_source_rows: list[dict[str, Any]] = []
    case_summaries: list[dict[str, Any]] = []
    for case in _select_cases(case_name):
        case_started = time.perf_counter()
        source_index, display_count, inline_count = _source_index(
            case,
            max_pages,
            match_scope,
            start_page=start_page,
        )
        source_rows = [{"case": case.name, **asdict(item)} for item in source_index]
        all_source_rows.extend(source_rows)
        source_index_path = manifest_dir / f"{case.name}_source_formulas.jsonl"
        _write_jsonl(source_index_path, source_rows)
        page_nums = _case_pages(case.pdf, start_page=start_page, max_pages=max_pages)
        source_hash = _file_hash(source_index_path)
        manifest = _load_manifest(manifest_dir / f"{case.name}.manifest.json")
        completed = (
            _completed_pages(manifest, source_hash=source_hash)
            | _completed_shard_pages(
                shard_dir,
                case.name,
                page_nums,
                source_hash=source_hash,
                preprocess_version=SHARD_PREPROCESS_VERSION,
            )
        ) if resume else set()
        todo_pages = [page for page in page_nums if page not in completed]
        results = _run_page_workers(
            case_name=case.name,
            pdf=str(case.pdf),
            latex_root=str(case.latex_root),
            source_index_path=str(source_index_path),
            source_hash=source_hash,
            output_dir=str(shard_dir),
            page_nums=todo_pages,
            workers=workers,
        )
        manifest_payload = _update_manifest(
            manifest,
            case_name=case.name,
            pdf=str(case.pdf),
            latex_root=str(case.latex_root),
            doc_hash=compute_sha256(str(case.pdf))[:16],
            source_hash=source_hash,
            page_nums=page_nums,
            results=results,
        )
        _write_json(manifest_dir / f"{case.name}.manifest.json", manifest_payload)
        merged = _merge_case_shards(case.name, shard_dir, page_nums)
        case_summaries.append(
            {
                "case": case.name,
                "elapsed_sec": round(time.perf_counter() - case_started, 3),
                "pages_total": len(page_nums),
                "pages_completed_before": len(completed),
                "pages_processed_now": len(todo_pages),
                "source_formulas": len(source_index),
                "source_display_formulas": display_count,
                "source_inline_formulas": inline_count,
                "candidates": len(merged["candidate_rows"]),
                "feature_graphs": len(merged["feature_rows"]),
                "raw_unknown_glyphs": sum(item.get("raw_unknown_glyphs", 0) for item in merged["page_summaries"]),
                "repaired_glyphs": sum(item.get("repaired_glyphs", 0) for item in merged["page_summaries"]),
                "feature_edges": sum(item.get("feature_edges", 0) for item in merged["page_summaries"]),
                "failed_pages": [
                    item for item in manifest_payload.get("pages", [])
                    if isinstance(item, dict) and item.get("status") == "failed"
                ],
            }
        )

    consolidated = _consolidate(dataset_dir, shard_dir, all_source_rows)
    rows, training_report = build_training_rows(dataset_dir)
    training_dir = output_dir / "training"
    _write_jsonl(training_dir / "tinybdmath_rows.jsonl", [asdict(row) for row in rows])
    _write_json(training_dir / "tinybdmath_report.json", asdict(training_report))
    audit = audit_dataset(dataset_dir, training_dir)
    _write_json(output_dir / "dataset_audit.json", audit)
    gold_audit = audit_gold(dataset_dir)
    _write_json(output_dir / "gold_audit.json", gold_audit)
    summary = {
        "schema_version": MANIFEST_VERSION,
        "preprocess_version": SHARD_PREPROCESS_VERSION,
        "case": case_name,
        "output_dir": str(output_dir),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "workers": max(1, int(workers)),
        "resume": resume,
        "cases": case_summaries,
        "consolidated": consolidated,
        "training": asdict(training_report),
        "audit": audit,
        "gold_audit": gold_audit,
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def _run_page_workers(
    *,
    case_name: str,
    pdf: str,
    latex_root: str,
    source_index_path: str,
    source_hash: str,
    output_dir: str,
    page_nums: list[int],
    workers: int,
) -> list[PageShardResult]:
    if not page_nums:
        return []
    args = [
        {
            "case_name": case_name,
            "pdf": pdf,
            "latex_root": latex_root,
            "source_index_path": source_index_path,
            "source_hash": source_hash,
            "output_dir": output_dir,
            "page_num": page_num,
        }
        for page_num in page_nums
    ]
    if workers <= 1:
        return [_build_page_shard(arg) for arg in args]
    results: list[PageShardResult] = []
    with ProcessPoolExecutor(max_workers=max(1, int(workers))) as executor:
        futures = [executor.submit(_build_page_shard, arg) for arg in args]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.page_num)


def _build_page_shard(arg: dict[str, Any]) -> PageShardResult:
    started = time.perf_counter()
    case_name = str(arg["case_name"])
    page_num = int(arg["page_num"])
    output_dir = Path(str(arg["output_dir"]))
    shard_path = _page_shard_path(output_dir, case_name, page_num)
    try:
        source_rows = _read_jsonl(Path(str(arg["source_index_path"])))
        source_index = [
            SourceFormulaRecord(
                source_id=str(row.get("source_id", "")),
                kind=str(row.get("kind", "")),
                latex=str(row.get("latex", "")),
                normalized=str(row.get("normalized", "")),
                token_count=int(row.get("token_count", 0) or 0),
                tex_path=str(row.get("tex_path", "")),
                tex_order=int(row.get("tex_order", 0) or 0),
                char_start=int(row.get("char_start", 0) or 0),
                char_end=int(row.get("char_end", 0) or 0),
                env=str(row.get("env", "")),
                delimiter=str(row.get("delimiter", "")),
                parser_version=str(row.get("parser_version", "")),
                context_before=str(row.get("context_before", "")),
                context_after=str(row.get("context_after", "")),
                pdf_page_hint=_optional_int(row.get("pdf_page_hint")),
                pdf_page_window_start=_optional_int(row.get("pdf_page_window_start")),
                pdf_page_window_end=_optional_int(row.get("pdf_page_window_end")),
                page_alignment_source=str(row.get("page_alignment_source", "")),
            )
            for row in source_rows
        ]
        match_index = _SourceMatchIndex(source_index)
        page_extractor = MuPDFBornDigitalExtractor()
        raw_extractor = RawGlyphGraphExtractor()
        structure_extractor = BornDigitalFormulaStructureExtractor(min_confidence=0.0)
        repairer = SymbolIdentityRepairer(auto_discover_glyph_maps=True)
        feature_extractor = TinyBDFeatureExtractor()
        doc = fitz.open(str(arg["pdf"]))
        try:
            page_facts = page_extractor.extract_page(doc[page_num], page_num)
        finally:
            doc.close()
        raw_graph = raw_extractor.from_page_facts(page_facts)
        enriched_graph = repairer.repair_graph(raw_graph)
        candidates = structure_extractor.extract_candidates_from_page_graphs(page_facts, raw_graph, enriched_graph)
        candidate_rows: list[dict[str, Any]] = []
        feature_rows: list[dict[str, Any]] = []
        edge_totals: Counter[str] = Counter()
        feature_edges = 0
        for candidate in candidates:
            feature_graph = feature_extractor.extract_region(enriched_graph, candidate.bbox)
            hint_counts = _edge_hint_counts(feature_graph)
            edge_totals.update(hint_counts)
            feature_edges += len(feature_graph.edges)
            match = match_index.best_match(candidate.latex, page_num=page_num)
            feature_rows.append(
                {
                    "case": case_name,
                    "candidate_id": candidate.candidate_id,
                    "page_num": page_num,
                    "bbox": candidate.bbox,
                    "feature_graph": feature_graph.to_json(),
                }
            )
            candidate_rows.append(
                {
                    "case": case_name,
                    **asdict(
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
                            feature_graph_jsonl_id=f"{case_name}:{candidate.candidate_id}",
                            best_source_similarity=float(match["similarity"]),
                            best_source_id=str(match["source_id"]),
                            best_source_latex=str(match["latex"]),
                            match_rank=int(match["rank"]),
                            page_match=str(match.get("page_match", "")),
                            source_pdf_page_hint=_optional_int(match.get("pdf_page_hint")),
                            source_pdf_page_window_start=_optional_int(match.get("pdf_page_window_start")),
                            source_pdf_page_window_end=_optional_int(match.get("pdf_page_window_end")),
                            warnings=list(candidate.warnings),
                        )
                    ),
                }
            )
        payload = {
            "schema_version": MANIFEST_VERSION,
            "preprocess_version": SHARD_PREPROCESS_VERSION,
            "case": case_name,
            "page_num": page_num,
            "source_hash": str(arg["source_hash"]),
            "status": "done",
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "raw_unknown_glyphs": raw_graph.health.unknown_glyph_count,
            "repaired_glyphs": enriched_graph.summary.repaired_count,
            "candidates": len(candidate_rows),
            "feature_edges": feature_edges,
            "edge_hint_totals": dict(sorted(edge_totals.items())),
            "candidate_rows": candidate_rows,
            "feature_rows": feature_rows,
        }
        _write_json(shard_path, payload)
        return PageShardResult(
            case=case_name,
            page_num=page_num,
            status="done",
            elapsed_sec=float(payload["elapsed_sec"]),
            raw_unknown_glyphs=raw_graph.health.unknown_glyph_count,
            repaired_glyphs=enriched_graph.summary.repaired_count,
            candidates=len(candidate_rows),
            feature_edges=feature_edges,
        )
    except Exception as exc:
        payload = {
            "schema_version": MANIFEST_VERSION,
            "preprocess_version": SHARD_PREPROCESS_VERSION,
            "case": case_name,
            "page_num": page_num,
            "status": "failed",
            "elapsed_sec": round(time.perf_counter() - started, 3),
            "error": str(exc),
            "candidate_rows": [],
            "feature_rows": [],
        }
        _write_json(shard_path, payload)
        return PageShardResult(
            case=case_name,
            page_num=page_num,
            status="failed",
            elapsed_sec=float(payload["elapsed_sec"]),
            raw_unknown_glyphs=0,
            repaired_glyphs=0,
            candidates=0,
            feature_edges=0,
            error=str(exc),
        )


def _consolidate(dataset_dir: Path, shard_dir: Path, source_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    for shard in sorted(shard_dir.glob("*/page_*.json")):
        payload = _read_json(shard)
        if payload.get("status") != "done":
            continue
        if payload.get("preprocess_version") != SHARD_PREPROCESS_VERSION:
            continue
        candidate_rows.extend(row for row in payload.get("candidate_rows", []) if isinstance(row, dict))
        feature_rows.extend(row for row in payload.get("feature_rows", []) if isinstance(row, dict))
    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(dataset_dir / "source_formulas.jsonl", source_rows)
    _write_jsonl(dataset_dir / "pdf_candidates.jsonl", candidate_rows)
    _write_jsonl(dataset_dir / "feature_graphs.jsonl", feature_rows)
    return {
        "source_formulas": len(source_rows),
        "pdf_candidates": len(candidate_rows),
        "feature_graphs": len(feature_rows),
    }


def _merge_case_shards(case_name: str, shard_dir: Path, page_nums: list[int]) -> dict[str, Any]:
    candidate_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    for page_num in page_nums:
        payload = _read_json(_page_shard_path(shard_dir, case_name, page_num))
        if payload.get("status") != "done" or payload.get("preprocess_version") != SHARD_PREPROCESS_VERSION:
            page_summaries.append({"page_num": page_num, "status": payload.get("status", "missing")})
            continue
        candidate_rows.extend(row for row in payload.get("candidate_rows", []) if isinstance(row, dict))
        feature_rows.extend(row for row in payload.get("feature_rows", []) if isinstance(row, dict))
        page_summaries.append(
            {
                "page_num": page_num,
                "status": "done",
                "raw_unknown_glyphs": int(payload.get("raw_unknown_glyphs", 0) or 0),
                "repaired_glyphs": int(payload.get("repaired_glyphs", 0) or 0),
                "feature_edges": int(payload.get("feature_edges", 0) or 0),
            }
        )
    return {"candidate_rows": candidate_rows, "feature_rows": feature_rows, "page_summaries": page_summaries}


def _case_pages(pdf: Path, *, start_page: int, max_pages: int) -> list[int]:
    doc = fitz.open(pdf)
    try:
        first = max(0, min(start_page, doc.page_count))
        last = doc.page_count if max_pages <= 0 else min(doc.page_count, first + max_pages)
        return list(range(first, last))
    finally:
        doc.close()


def _page_shard_path(root: Path, case_name: str, page_num: int) -> Path:
    return root / case_name / f"page_{page_num:06d}.json"


def _completed_pages(manifest: dict[str, Any], *, source_hash: str) -> set[int]:
    result: set[int] = set()
    if manifest.get("source_hash") != source_hash:
        return result
    for item in manifest.get("pages", []):
        if not isinstance(item, dict) or item.get("status") != "done":
            continue
        try:
            result.add(int(item.get("page_num")))
        except (TypeError, ValueError):
            continue
    return result


def _completed_shard_pages(
    shard_dir: Path,
    case_name: str,
    page_nums: list[int],
    *,
    source_hash: str,
    preprocess_version: str,
) -> set[int]:
    result: set[int] = set()
    for page in page_nums:
        payload = _read_json(_page_shard_path(shard_dir, case_name, page))
        if (
            payload.get("status") == "done"
            and payload.get("source_hash") == source_hash
            and payload.get("preprocess_version") == preprocess_version
        ):
            result.add(page)
    return result


def _update_manifest(
    manifest: dict[str, Any],
    *,
    case_name: str,
    pdf: str,
    latex_root: str,
    doc_hash: str,
    source_hash: str,
    page_nums: list[int],
    results: list[PageShardResult],
) -> dict[str, Any]:
    by_page: dict[int, dict[str, Any]] = {}
    if manifest.get("source_hash") == source_hash and manifest.get("preprocess_version") == SHARD_PREPROCESS_VERSION:
        for item in manifest.get("pages", []):
            if isinstance(item, dict):
                try:
                    by_page[int(item.get("page_num"))] = item
                except (TypeError, ValueError):
                    pass
    for result in results:
        by_page[result.page_num] = asdict(result)
    pages = [by_page.get(page, {"page_num": page, "status": "queued"}) for page in page_nums]
    return {
        "schema_version": MANIFEST_VERSION,
        "preprocess_version": SHARD_PREPROCESS_VERSION,
        "case": case_name,
        "pdf": pdf,
        "latex_root": latex_root,
        "doc_hash": doc_hash,
        "source_hash": source_hash,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pages": pages,
        "counts": dict(sorted(Counter(str(item.get("status", "")) for item in pages).items())),
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    return _read_json(path) if path.exists() else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_hash(path: Path) -> str:
    return compute_sha256(str(path))[:16]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=Path("test_artifacts/tinybdmath_sharded"))
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--match-scope", choices=["all", "display", "inline"], default="all")
    args = parser.parse_args()

    payload = build_sharded_dataset(
        case_name=args.case,
        output_dir=args.output_dir,
        start_page=args.start_page,
        max_pages=args.max_pages,
        workers=max(1, args.workers),
        resume=not args.no_resume,
        match_scope=args.match_scope,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
