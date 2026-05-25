"""Prepare TinyBDMath training/evaluation rows from real dataset JSONL files.

The input is produced by ``tools/born_digital_formula_dataset.py`` from the
bundled Attention/Napkin PDF + LaTeX sources.  This script does not train a
model and does not require heavy ML dependencies; it creates auditable rows
for later MLP/GNN experiments.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tinybdmath_gold_policy import source_maps, verified_gold_blockers


@dataclass
class TinyBDTrainingRow:
    row_id: str
    case: str
    candidate_id: str
    page_num: int
    quality_label: str
    verified_gold: bool
    verified_gold_blockers: list[str]
    source_similarity: float
    best_source_id: str
    page_match: str
    glyph_count: int
    edge_count: int
    edge_hint_counts: dict[str, int]
    feature_density: float
    structural_signal_count: int
    unknown_glyph_rate: float
    repaired_count: int
    feature_graph_hash: str
    r0_input_hash: str
    enriched_graph_hash: str
    latex_target: str
    raw_source_latex: str
    source_macro_expansion_warnings: list[str]
    pdf_text: str


@dataclass
class TinyBDTrainingReport:
    dataset_dir: str
    rows: int
    cases: dict[str, int]
    quality_counts: dict[str, int]
    average_similarity: float
    average_glyphs: float
    average_edges: float
    edge_hint_totals: dict[str, int]
    verified_gold_rows: int
    verified_gold_blockers: dict[str, int]
    low_quality_samples: list[dict[str, Any]]


def build_training_rows(dataset_dir: Path) -> tuple[list[TinyBDTrainingRow], TinyBDTrainingReport]:
    sources = _read_jsonl(dataset_dir / "source_formulas.jsonl")
    candidates = _read_jsonl(dataset_dir / "pdf_candidates.jsonl")
    source_by_key, source_scope_counts = source_maps(sources)
    rows: list[TinyBDTrainingRow] = []
    for index, candidate in enumerate(candidates):
        similarity = _float(candidate.get("best_source_similarity"))
        summary = candidate.get("enriched_summary", {})
        if not isinstance(summary, dict):
            summary = {}
        glyph_count = _int(candidate.get("glyph_count"))
        edge_count = _int(candidate.get("edge_count"))
        unknown_before = _int(summary.get("unknown_before"))
        unknown_after = _int(summary.get("unknown_after"))
        repaired_count = _int(summary.get("repaired_count"))
        unknown_rate = unknown_after / glyph_count if glyph_count else 0.0
        blockers = verified_gold_blockers(
            candidate,
            source_by_key,
            source_scope_counts,
        )
        rows.append(
            TinyBDTrainingRow(
                row_id=f"tinybd_{index:08d}",
                case=str(candidate.get("case", "")),
                candidate_id=str(candidate.get("candidate_id", "")),
                page_num=_int(candidate.get("page_num")),
                quality_label=_quality_label(similarity, glyph_count, edge_count, unknown_before, unknown_after),
                verified_gold=not blockers,
                verified_gold_blockers=blockers,
                source_similarity=similarity,
                best_source_id=str(candidate.get("best_source_id", "")),
                page_match=str(candidate.get("page_match", "")),
                glyph_count=glyph_count,
                edge_count=edge_count,
                edge_hint_counts=_dict_int(candidate.get("edge_hint_counts")),
                feature_density=round(edge_count / glyph_count, 6) if glyph_count else 0.0,
                structural_signal_count=_structural_signal_count(candidate.get("edge_hint_counts")),
                unknown_glyph_rate=round(unknown_rate, 6),
                repaired_count=repaired_count,
                feature_graph_hash=str(candidate.get("feature_graph_hash", "")),
                r0_input_hash=str(candidate.get("r0_input_hash", "")),
                enriched_graph_hash=str(candidate.get("enriched_graph_hash", "")),
                latex_target=str(candidate.get("best_source_latex", "")),
                raw_source_latex=str(candidate.get("best_source_raw_latex", "")),
                source_macro_expansion_warnings=[
                    str(item) for item in candidate.get("source_macro_expansion_warnings", []) if item
                ],
                pdf_text=str(candidate.get("pdf_text", "")),
            )
        )
    report = _report(dataset_dir, rows)
    return rows, report


def _quality_label(
    similarity: float,
    glyph_count: int,
    edge_count: int,
    unknown_before: int,
    unknown_after: int,
) -> str:
    if glyph_count <= 0 or edge_count <= 0:
        return "unusable_empty_features"
    if unknown_after > 0:
        return "needs_symbol_repair"
    if similarity >= 0.92:
        return "strong_alignment"
    if similarity >= 0.80:
        return "near_alignment"
    if similarity >= 0.55:
        return "weak_alignment"
    if unknown_before > 0:
        return "weak_with_unknown_source"
    return "low_alignment"


def _report(dataset_dir: Path, rows: list[TinyBDTrainingRow]) -> TinyBDTrainingReport:
    cases = Counter(row.case for row in rows)
    quality = Counter(row.quality_label for row in rows)
    verified_blockers: Counter[str] = Counter()
    hint_totals: Counter[str] = Counter()
    for row in rows:
        hint_totals.update(row.edge_hint_counts)
        verified_blockers.update(row.verified_gold_blockers)
    similarities = [row.source_similarity for row in rows]
    low_samples = [
        {
            "case": row.case,
            "candidate_id": row.candidate_id,
            "page_num": row.page_num,
            "quality_label": row.quality_label,
            "verified_gold": row.verified_gold,
            "verified_gold_blockers": row.verified_gold_blockers,
            "source_similarity": row.source_similarity,
            "page_match": row.page_match,
            "glyph_count": row.glyph_count,
            "edge_count": row.edge_count,
            "pdf_text": row.pdf_text[:160],
            "latex_target": row.latex_target[:160],
            "raw_source_latex": row.raw_source_latex[:160],
            "source_macro_expansion_warnings": row.source_macro_expansion_warnings,
        }
        for row in rows
        if row.quality_label in {"low_alignment", "weak_with_unknown_source", "unusable_empty_features"}
    ][:50]
    return TinyBDTrainingReport(
        dataset_dir=str(dataset_dir),
        rows=len(rows),
        cases=dict(sorted(cases.items())),
        quality_counts=dict(sorted(quality.items())),
        average_similarity=round(sum(similarities) / len(similarities), 3) if similarities else 1.0,
        average_glyphs=round(sum(row.glyph_count for row in rows) / len(rows), 3) if rows else 0.0,
        average_edges=round(sum(row.edge_count for row in rows) / len(rows), 3) if rows else 0.0,
        edge_hint_totals=dict(sorted(hint_totals.items())),
        verified_gold_rows=sum(1 for row in rows if row.verified_gold),
        verified_gold_blockers=dict(sorted(verified_blockers.items())),
        low_quality_samples=low_samples,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dict_int(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _int(item) for key, item in value.items()}


def _structural_signal_count(value: object) -> int:
    hints = _dict_int(value)
    return (
        hints.get("subscript_zone", 0)
        + hints.get("superscript_zone", 0)
        + hints.get("above_zone", 0)
        + hints.get("below_zone", 0)
        + hints.get("overlap_zone", 0)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows, report = build_training_rows(args.dataset_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.output_dir / "tinybdmath_rows.jsonl", [asdict(row) for row in rows])
    (args.output_dir / "tinybdmath_report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
